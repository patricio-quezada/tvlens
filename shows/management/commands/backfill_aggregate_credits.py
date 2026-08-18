"""
Management command to backfill episode counts from TMDB aggregate_credits.

Fetches only /tv/{id}/aggregate_credits for each show. It does not re-fetch
details, seasons, or episodes, so a full catalog pass costs one API call per
show rather than the hundreds a full re-ingest would.

Usage:
    python manage.py backfill_aggregate_credits              # whole catalog
    python manage.py backfill_aggregate_credits --show 1396  # Breaking Bad
    python manage.py backfill_aggregate_credits --limit 5 --dry-run
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from shows.ingestion import Ingestor
from shows.models import Show


class Command(BaseCommand):
    help = "Fill episode_count on cast and crew from TMDB aggregate_credits"

    def add_arguments(self, parser):
        parser.add_argument(
            "--show",
            type=int,
            default=None,
            help="Backfill a single show by TMDB ID",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many shows",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and report counts without writing anything",
        )

    def handle(self, *args, **options):
        shows = Show.objects.order_by("pk")
        if options["show"]:
            shows = shows.filter(tmdb_id=options["show"])
            if not shows.exists():
                self.stdout.write(
                    self.style.ERROR(f"No show with TMDB ID {options['show']}")
                )
                return
        if options["limit"]:
            shows = shows[: options["limit"]]

        dry_run = options["dry_run"]
        ingestor = Ingestor()
        total_cast = total_crew = 0
        empty = []

        for i, show in enumerate(shows, start=1):
            if dry_run:
                agg = ingestor.client.get_tv_aggregate_credits(show.tmdb_id)
                if not agg:
                    empty.append(show.name)
                    self.stdout.write(f"{i:4d}. {show.name}: no data")
                    continue
                cast = sum(len(p.get("roles", [])) for p in agg.get("cast", []))
                crew = sum(len(p.get("jobs", [])) for p in agg.get("crew", []))
            else:
                cast, crew = ingestor.ingest_aggregate_credits(show)
                if (cast, crew) == (0, 0):
                    empty.append(show.name)

            total_cast += cast
            total_crew += crew
            self.stdout.write(f"{i:4d}. {show.name}: {cast} cast, {crew} crew")

        verb = "Would write" if dry_run else "Wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {total_cast} cast rows and {total_crew} crew rows "
                f"across {len(shows)} shows."
            )
        )
        if empty:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(empty)} returned nothing: {', '.join(empty)}"
                )
            )

        # Backfilled episode counts change every weighted score, so refresh the
        # materialized Layer 1 store once the whole pass is written. A dry run
        # wrote nothing, so it leaves the store alone (ADR-07).
        if not dry_run:
            call_command("rebuild_similar_shows")
