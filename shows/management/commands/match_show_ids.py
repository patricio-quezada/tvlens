"""
Make Show.id equal Show.tmdb_id, once, so a show has one identity everywhere.

Show.id is a BigAutoField: it records the order rows happened to be inserted on
one machine and means nothing anywhere else. Show.tmdb_id is the same number on
every machine, forever, and is already unique and indexed.

While TVLens runs as one deployment that distinction is harmless. It stops
being harmless the moment a catalog is shipped into a database that already
holds ratings, because a fresh ingest assigns different surrogate ids and every
rating would silently re-point at the wrong show. Ratings are the one thing in
TVLens that cannot be regenerated.

This is a data operation, not a schema migration: no ALTER, no column change,
nothing irreversible in the schema. It rewrites id values and the foreign keys
that follow them.

Two phases, because the ranges overlap. On this catalog 8 values were both an
existing id and some show's tmdb_id, so a single pass would collide mid-flight.
Everything is first shifted above max(id, tmdb_id), then brought down onto its
tmdb_id.

The check that matters runs before and after: every rating must resolve to the
same TMDB ID it did to begin with. If it does not, nothing is committed.

Usage:
    python manage.py match_show_ids --dry-run
    python manage.py match_show_ids
"""

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from shows.models import Rating, Review, Show, ShowTag, Watchlist


def child_columns():
    """Every (table, column) with a foreign key to shows_show, from the schema.

    Read rather than listed. The hand-written list missed the two many-to-many
    join tables, shows_show_genres and shows_show_networks, which have no model
    of their own and are easy to forget precisely because Django hides them.
    The integrity check caught it and the transaction rolled back, but asking
    SQLite is the version that cannot be incomplete.
    """
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]
    found = []
    for table in tables:
        cursor.execute(f"PRAGMA foreign_key_list({table})")
        for row in cursor.fetchall():
            if row[2] == "shows_show":
                found.append((table, row[3]))
    return found


def rating_fingerprint():
    """Every user rating keyed by TMDB ID. The thing that must not change."""
    return sorted(
        (r.user_id, r.show.tmdb_id, r.score) for r in Rating.objects.select_related("show")
    )


class Command(BaseCommand):
    help = "Rewrite Show.id to equal Show.tmdb_id, preserving every rating."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the plan without writing anything.",
        )

    def handle(self, *args, **options):
        shows = list(Show.objects.values_list("id", "tmdb_id"))
        pending = [(old, new) for old, new in shows if old != new]

        if not pending:
            self.stdout.write("Every Show.id already equals its tmdb_id. Nothing to do.")
            return

        overlap = {old for old, _ in shows} & {new for _, new in shows}
        offset = max(max(o for o, _ in shows), max(n for _, n in shows)) + 1

        self.stdout.write(f"Shows to rewrite: {len(pending)} of {len(shows)}")
        self.stdout.write(f"  ids that collide with some tmdb_id: {len(overlap)}")
        self.stdout.write(f"  shift offset: {offset}")
        self.stdout.write(
            f"  rows following them: {Rating.objects.count()} ratings, "
            f"{Review.objects.count()} reviews, {Watchlist.objects.count()} watchlist, "
            f"{ShowTag.objects.count()} tags"
        )

        before = rating_fingerprint()
        self.stdout.write(f"  fingerprint: {len(before)} ratings by TMDB ID")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run. Nothing written."))
            return

        columns = child_columns()
        self.stdout.write(f"  tables following Show.id: {len(columns)}")

        with transaction.atomic():
            with connection.constraint_checks_disabled():
                cursor = connection.cursor()
                # Phase 1: lift everything clear of both ranges.
                for table, column in columns:
                    cursor.execute(f"UPDATE {table} SET {column} = {column} + %s", [offset])
                cursor.execute("UPDATE shows_show SET id = id + %s", [offset])

                # Phase 2: bring each row down onto its own tmdb_id.
                for old, new in shows:
                    shifted = old + offset
                    for table, column in columns:
                        cursor.execute(
                            f"UPDATE {table} SET {column} = %s WHERE {column} = %s",
                            [new, shifted],
                        )
                    cursor.execute("UPDATE shows_show SET id = %s WHERE id = %s", [new, shifted])

            connection.check_constraints()

            after = rating_fingerprint()
            if after != before:
                raise RuntimeError(
                    "Rating fingerprint changed. Rolling back and leaving the data alone."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nRewrote {len(pending)} show ids. "
                f"All {len(after)} ratings resolve to the same TMDB ID as before."
            )
        )
        self.stdout.write(
            "Sequence note: new shows must be created with an explicit id "
            "(see Ingestor.ingest_show), or an autofield will hand out a low "
            "integer that collides with a real tmdb_id."
        )
