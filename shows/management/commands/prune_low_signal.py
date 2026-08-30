"""
Remove shows the people graph cannot use, and the rows hanging off them.

ingest_discover now filters at the API (see MIN_VOTE_COUNT), so nothing below
the floor arrives any more. This is the one-time catch-up for a catalog ingested
before that filter existed.

What it removes is specific: news bulletins, soaps and game shows. On the first
464 shows, 124 of them carried 82% of all 487,000 episode rows at a median
vote_count of 18 against a catalog median of 137, and mostly produced no Layer 1
edges at all. They cost storage and query time and returned nothing.

Deleting a Show cascades to its seasons, episodes, cast, crew, ratings and
edges. People are not owned by a show, so anyone left crediting nothing is
swept separately.

This is destructive and re-ingesting is the only way back, so --dry-run reports
without touching anything and is the sensible first run.

Usage:
    python manage.py prune_low_signal --dry-run
    python manage.py prune_low_signal
    python manage.py prune_low_signal --min-votes 250
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from shows.ingestion import MIN_VOTE_COUNT
from shows.models import (
    CastMember,
    CrewMember,
    Person,
    Rating,
    Review,
    Show,
    ShowTag,
    SimilarShow,
    Watchlist,
)


class Command(BaseCommand):
    help = "Delete shows below the vote_count floor, and any people left crediting nothing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would go without deleting anything.",
        )
        parser.add_argument(
            "--min-votes",
            type=int,
            default=MIN_VOTE_COUNT,
            help=f"The floor (default {MIN_VOTE_COUNT}).",
        )
        parser.add_argument(
            "--skip-rebuild",
            action="store_true",
            help="Do not rebuild the Layer 1 graph afterwards.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        floor = options["min_votes"]

        # A show someone has engaged with is never pruned, however far its
        # vote_count has fallen. Popularity is TMDb's opinion; a rating is the
        # user's, and deleting a Show cascades to the Ratings hanging off it.
        # Losing someone's 5.0 because a show got less popular is the one
        # outcome this command must never produce.
        spoken_for = (
            set(Rating.objects.values_list("show_id", flat=True))
            | set(Review.objects.values_list("show_id", flat=True))
            | set(Watchlist.objects.values_list("show_id", flat=True))
            | set(ShowTag.objects.values_list("show_id", flat=True))
        )
        doomed = Show.objects.filter(vote_count__lt=floor).exclude(id__in=spoken_for)
        spared = Show.objects.filter(vote_count__lt=floor, id__in=spoken_for).count()
        if spared:
            self.stdout.write(
                self.style.WARNING(
                    f"Sparing {spared} show(s) below the floor that someone has rated, "
                    "reviewed, listed or tagged."
                )
            )
        if not doomed.exists():
            self.stdout.write(f"Nothing below vote_count {floor}. Catalog is already clean.")
            return

        episodes = sum(s.n for s in doomed.annotate(n=Count("seasons__episodes")))
        ratings = Rating.objects.filter(show__in=doomed).count()
        edges = (
            (
                SimilarShow.objects.filter(source__in=doomed)
                | SimilarShow.objects.filter(target__in=doomed)
            )
            .distinct()
            .count()
        )

        self.stdout.write(f"Floor: vote_count >= {floor}")
        self.stdout.write(f"  shows      {doomed.count():>7} of {Show.objects.count()}")
        self.stdout.write(f"  episodes   {episodes:>7}")
        self.stdout.write(f"  ratings    {ratings:>7}")
        self.stdout.write(f"  edges      {edges:>7} of {SimilarShow.objects.count()}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run. Nothing deleted."))
            return

        show_count = doomed.count()
        with transaction.atomic():
            doomed.delete()
            # A Person belongs to no show, so nobody cascades. Sweep the ones
            # whose every credit went with the shows just removed.
            orphans = Person.objects.exclude(pk__in=CastMember.objects.values("person_id")).exclude(
                pk__in=CrewMember.objects.values("person_id")
            )
            people = orphans.count()
            orphans.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nRemoved {show_count} shows, {episodes} episodes, "
                f"{people} people crediting nothing."
            )
        )

        if not options["skip_rebuild"]:
            self.stdout.write("Rebuilding the Layer 1 graph (ADR-07) ...")
            call_command("rebuild_similar_shows")
