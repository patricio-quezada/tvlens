"""
Management command to ingest TV shows from TMDB.

Usage:
    python manage.py ingest_shows                # 5 pages (default)
    python manage.py ingest_shows --pages 20     # 20 pages (~400 shows)
    python manage.py ingest_shows --show 1396    # single show by TMDB ID
"""

from django.core.management.base import BaseCommand

from shows.ingestion import Ingestor


class Command(BaseCommand):
    help = "Ingest TV show data from TMDB into the local database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pages",
            type=int,
            default=5,
            help="Number of Discover pages to pull (20 shows per page)",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=None,
            help="Ingest a single show by TMDB ID",
        )
        parser.add_argument(
            "--sort-by",
            type=str,
            default="popularity.desc",
            help="Sort order for Discover endpoint",
        )

    def handle(self, *args, **options):
        ingestor = Ingestor()

        if options["show"]:
            self.stdout.write(f"Ingesting single show: {options['show']}")
            show = ingestor.ingest_show(options["show"])
            if show:
                self.stdout.write(self.style.SUCCESS(f"Done: {show.name}"))
            else:
                self.stdout.write(self.style.ERROR("Failed to ingest show"))
            return

        pages = options["pages"]
        sort_by = options["sort_by"]
        self.stdout.write(f"Ingesting {pages} pages (sort: {sort_by}) ...")
        ingestor.ingest_discover(pages=pages, sort_by=sort_by)
        self.stdout.write(self.style.SUCCESS("Ingestion complete."))
