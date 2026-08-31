"""
Ingest one show by TMDb id, deliberately.

ingest_discover walks Discover sorted by popularity, so the catalog is "the
most popular N shows" rather than "shows worth having". A show can clear the
vote floor by seven times over and still never be fetched, because it never
reached the pages we pull. Bluey has 735 votes and an 8.6 average and was
absent on 2026-08-31 for exactly this reason.

This is the deliberate path in: name a show and it is ingested, floor or no
floor. Use it for the shows you know should be there.

Finding the id: TMDb search is not on the client, so pass an id you already
have, or use --search to look one up first.

Usage:
    python manage.py ingest_show 82728
    python manage.py ingest_show --search "Bluey"
    python manage.py ingest_show 82728 --skip-rebuild
"""

import requests
from django.core.management import call_command
from django.core.management.base import BaseCommand

from shows.ingestion import Ingestor
from shows.models import Show


class Command(BaseCommand):
    help = "Ingest one show by TMDb id, bypassing the Discover sweep."

    def add_arguments(self, parser):
        parser.add_argument("tmdb_id", nargs="?", type=int, help="TMDb show id.")
        parser.add_argument("--search", help="Look up ids by name and stop.")
        parser.add_argument(
            "--skip-rebuild",
            action="store_true",
            help="Do not rebuild the Layer 1 graph afterwards. The show will "
            "have no edges until you do.",
        )

    def handle(self, *args, **options):
        ingestor = Ingestor()

        if options["search"]:
            client = ingestor.client
            r = requests.get(
                f"{client.base_url}/search/tv",
                params={"api_key": client.api_key, "query": options["search"]},
                timeout=20,
            )
            results = r.json().get("results", [])[:10]
            if not results:
                self.stdout.write(self.style.WARNING("Nothing found."))
                return
            have = set(Show.objects.values_list("id", flat=True))
            for s in results:
                mark = "in catalog" if s["id"] in have else ""
                year = (s.get("first_air_date") or "")[:4]
                self.stdout.write(
                    f"  {s['id']:<8} {str(s.get('vote_count') or 0):>6} votes  "
                    f"{s.get('vote_average') or 0:>4}  {s['name'][:44]} ({year})  {mark}"
                )
            self.stdout.write("\nThen: python manage.py ingest_show <id>")
            return

        tmdb_id = options["tmdb_id"]
        if not tmdb_id:
            self.stdout.write(self.style.ERROR("Pass a TMDb id, or --search to find one."))
            return

        if Show.objects.filter(id=tmdb_id).exists():
            show = Show.objects.get(id=tmdb_id)
            self.stdout.write(f"Already in the catalog: {show.name}. Re-ingesting to refresh it.")

        show = ingestor.ingest_show(tmdb_id)
        if not show:
            self.stdout.write(self.style.ERROR(f"TMDb returned nothing for {tmdb_id}."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Ingested {show.name} ({show.vote_count} votes, {show.vote_average} avg)"
            )
        )

        if options["skip_rebuild"]:
            self.stdout.write(
                self.style.WARNING(
                    "Graph not rebuilt. This show has no connections and cannot be "
                    "recommended until you run rebuild_similar_shows."
                )
            )
            return

        # The rebuild is wholesale by design (ADR-07): every edge is deleted and
        # every source recomputed. There is no incremental path yet, which is
        # why on-demand ingestion during a search is not possible today.
        self.stdout.write("\nRebuilding the Layer 1 graph (ADR-07) ...")
        call_command("rebuild_similar_shows")
