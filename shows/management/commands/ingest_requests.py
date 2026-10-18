"""
Ingest the shows readers asked for from search (ShowRequest).

Run nightly by the refresh-catalog GitHub Action, before refresh_catalog.
Each requested TMDb id is ingested once, oldest request first, however many
readers asked for it. A request is done once its show exists, so there is no
status to update on success; an id TMDb returns nothing for is marked failed
so it is not retried every night.

Usage:
    python manage.py ingest_requests
    python manage.py ingest_requests --limit 5 --skip-rebuild
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from shows.ingestion import Ingestor
from shows.models import Show, ShowRequest


class Command(BaseCommand):
    help = "Ingest the shows readers requested from search."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Most shows to ingest in one run, so a long queue cannot outlast the job.",
        )
        parser.add_argument(
            "--skip-rebuild",
            action="store_true",
            help="Do not rebuild the Layer 1 graph afterwards.",
        )

    def handle(self, *args, **options):
        have = set(Show.objects.values_list("tmdb_id", flat=True))
        pending = []
        for tmdb_id in ShowRequest.objects.filter(failed=False).values_list("tmdb_id", flat=True):
            if tmdb_id not in have and tmdb_id not in pending:
                pending.append(tmdb_id)
        pending = pending[: options["limit"]]
        if not pending:
            self.stdout.write("No pending requests.")
            return

        ingestor = Ingestor()
        added = 0
        for tmdb_id in pending:
            show = ingestor.ingest_show(tmdb_id)
            if show:
                added += 1
                self.stdout.write(self.style.SUCCESS(f"Ingested {show.name} ({tmdb_id})"))
            else:
                ShowRequest.objects.filter(tmdb_id=tmdb_id).update(failed=True)
                self.stdout.write(self.style.WARNING(f"TMDb returned nothing for {tmdb_id}"))

        if added and not options["skip_rebuild"]:
            self.stdout.write("\nRebuilding the Layer 1 graph (ADR-07) ...")
            call_command("rebuild_similar_shows")
