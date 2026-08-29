"""
Create synthetic rated profiles so the recommender can be exercised at scale.

Every tuned constant in Layer 2 was fitted against one real user with ten
ratings: WATCH_NEXT_SEED_FLOOR, SIDE_QUEST_GENRE_EXPONENT, the three
connection-type gates. That is the largest untested assumption in the
recommender and no unit test catches it, because unit tests build the profile
they want to see rather than discovering what a real rating spread produces.

These profiles are DISPOSABLE and must never reach production. Two markers make
them unmistakable and easy to find: every username starts with SYNTHETIC_PREFIX
and every address uses the RFC 2606 reserved domain `.invalid`, which can never
resolve. Deleting the user cascades to ratings, reviews, watch history,
watchlist, and tags, so --remove is a complete cleanup.

The shapes are chosen to hit different parts of the recommender rather than to
look realistic:

    sparse      below every gate, should produce empty or locked rows
    flat        many ratings, almost no spread, should produce NO lean
    decisive    wide spread, should be the profile that trips a lean
    narrow      one genre rated highly, tests the Side Quests genre gate
    broad       spread across every genre, tests reranking with no focus
    heavy       the largest set, for query counts and timing
    cast-lean   built, not random: rates cast-tied neighbours high and
                crew-tied ones low, which is the ONLY way to trip ADR-15's
                lean gate. Random ratings never can, because they are
                uncorrelated with whether an edge is made of cast or crew.

Usage:
    python manage.py seed_test_profiles
    python manage.py seed_test_profiles --report
    python manage.py seed_test_profiles --remove
"""

import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from shows.models import Genre, Rating, Show, SimilarShow
from shows.recommenders import connection_type, role_indexes, shared_connections

SYNTHETIC_PREFIX = "synthetic-"
SYNTHETIC_DOMAIN = "synthetic.invalid"
SEED = 20260829

# name, how many shows to rate, (low, high) score range, genres to draw from
SHAPES = [
    ("sparse", 2, (3.5, 4.5), None),
    ("flat", 40, (3.5, 3.6), None),
    ("decisive", 40, (0.5, 5.0), None),
    ("narrow", 25, (4.0, 5.0), 1),
    ("broad", 60, (1.0, 5.0), None),
    ("heavy", 150, (0.5, 5.0), None),
]

# Built rather than sampled. See _build_cast_lean.
LEAN_SHAPE = "cast-lean"


class Command(BaseCommand):
    help = "Create or remove synthetic rated profiles for scale testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete every synthetic profile and everything it owns.",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="Show what exists without changing anything.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        existing = User.objects.filter(username__startswith=SYNTHETIC_PREFIX)

        if options["report"]:
            return self._report(existing)

        if options["remove"]:
            n = existing.count()
            ratings = Rating.objects.filter(user__in=existing).count()
            existing.delete()
            self.stdout.write(
                self.style.SUCCESS(f"Removed {n} synthetic profiles and {ratings} ratings.")
            )
            return

        if existing.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"{existing.count()} synthetic profiles already exist. "
                    "Run with --remove first to rebuild them."
                )
            )
            return

        shows = list(Show.objects.all())
        if not shows:
            self.stdout.write(self.style.ERROR("No shows in the catalog. Ingest first."))
            return

        rng = random.Random(SEED)
        genres = list(Genre.objects.all())

        with transaction.atomic():
            for name, count, (low, high), genre_n in SHAPES:
                user = User.objects.create_user(
                    username=f"{SYNTHETIC_PREFIX}{name}",
                    email=f"{name}@{SYNTHETIC_DOMAIN}",
                    password=User.objects.make_random_password()
                    if hasattr(User.objects, "make_random_password")
                    else rng.randbytes(16).hex(),
                )

                pool = shows
                if genre_n and genres:
                    picked = rng.sample(genres, min(genre_n, len(genres)))
                    pool = [s for s in shows if set(s.genres.all()) & set(picked)] or shows

                chosen = rng.sample(pool, min(count, len(pool)))
                Rating.objects.bulk_create(
                    [
                        Rating(
                            user=user,
                            show=show,
                            # half-star steps, the same scale the UI offers
                            score=round(rng.uniform(low, high) * 2) / 2,
                        )
                        for show in chosen
                    ]
                )
                self.stdout.write(f"  {user.username:<24} {len(chosen):3d} ratings")

            n = self._build_cast_lean(User, rng)
            self.stdout.write(f"  {SYNTHETIC_PREFIX}{LEAN_SHAPE:<15} {n:3d} ratings  (built)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nCreated {len(SHAPES) + 1} synthetic profiles. "
                "Remove them with: python manage.py seed_test_profiles --remove"
            )
        )
        self._report(User.objects.filter(username__startswith=SYNTHETIC_PREFIX))

    def _build_cast_lean(self, User, rng):
        """Rate cast-tied neighbours high and crew-tied ones low.

        ADR-15's lean gate compares the average rating signal that cast
        connections and crew connections have each earned. A random spread
        cannot move it, however wide: the scores carry no information about
        what an edge is made of, so both affinities converge on the same mean
        and the lean stays near zero. Measured on this catalog, six random
        profiles from 2 to 150 ratings all produced no lean at all.

        So this profile is constructed. For each stored edge inside a working
        set, shared_connections splits the contribution into cast and crew;
        whichever side carries more decides the score. That is the only shape
        that puts the third gate under test.
        """
        user = User.objects.create_user(
            username=f"{SYNTHETIC_PREFIX}{LEAN_SHAPE}",
            email=f"{LEAN_SHAPE}@{SYNTHETIC_DOMAIN}",
            password=rng.randbytes(16).hex(),
        )

        edges = list(SimilarShow.objects.values_list("source_id", "target_id")[:400])
        touched = {i for pair in edges for i in pair}
        shows = {s.id: s for s in Show.objects.filter(id__in=touched)}
        if not shows:
            return 0
        indexes = role_indexes(shows.values())

        verdict = {}
        for source_id, target_id in edges:
            if source_id not in shows or target_id not in shows:
                continue
            mass = {"cast": 0.0, "crew": 0.0}
            for c in shared_connections(
                shows[source_id], indexes[source_id], shows[target_id], indexes[target_id]
            ):
                mass[connection_type(c.kind)] += c.contribution
            if mass["cast"] == mass["crew"]:
                continue
            cast_led = mass["cast"] > mass["crew"]
            for sid in (source_id, target_id):
                verdict.setdefault(sid, []).append(cast_led)

        ratings = []
        for show_id, calls in verdict.items():
            cast_led = sum(calls) > len(calls) / 2
            ratings.append(Rating(user=user, show_id=show_id, score=5.0 if cast_led else 1.0))
        Rating.objects.bulk_create(ratings)
        return len(ratings)

    def _report(self, users):
        if not users.exists():
            self.stdout.write("No synthetic profiles exist.")
            return
        self.stdout.write("\nsynthetic profiles:")
        for u in users.order_by("username"):
            scores = list(Rating.objects.filter(user=u).values_list("score", flat=True))
            if not scores:
                self.stdout.write(f"  {u.username:<24}   0 ratings")
                continue
            spread = max(scores) - min(scores)
            mean = sum(scores) / len(scores)
            self.stdout.write(
                f"  {u.username:<24} {len(scores):3d} ratings  mean {mean:.2f}  spread {spread:.1f}"
            )
