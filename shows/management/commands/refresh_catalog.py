"""
Keep the catalog current, a bounded slice at a time.

TMDb moves under us. Shows are added, popularity and vote_count drift, seasons
air, casts get recast. A catalog ingested once is right on the day it is pulled
and slowly stops being right afterwards.

Two jobs with different costs, so they are separate flags rather than one
"refresh everything":

    --discover    one call per page, finds shows that have RISEN above the
                  vote floor since the last sweep. Cheap. Run it often.

    --oldest N    re-hydrates the N stalest shows in place. Roughly 2 calls
                  per show plus one per season, so a full pass over this
                  catalog is ~3,500 calls. Run it in slices.

Show.updated_at carries auto_now, so "stalest" needs no new column, and
ingest_show is update_or_create throughout, so re-running it on a known show
corrects the record rather than duplicating it.

A show whose vote_count has FALLEN below the floor is reported, not deleted.
Popularity is noisy and a show dipping under 100 for a week is not the same
decision as never ingesting it; prune_low_signal exists for when you mean it.

Usage:
    python manage.py refresh_catalog --oldest 20
    python manage.py refresh_catalog --discover --pages 3
    python manage.py refresh_catalog --discover --oldest 20
    python manage.py refresh_catalog --oldest 20 --dry-run
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from shows.ingestion import MIN_VOTE_COUNT, Ingestor
from shows.models import Show


class Command(BaseCommand):
    help = "Refresh the stalest shows and pull in newly qualifying ones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--oldest",
            type=int,
            default=0,
            help="Re-hydrate this many of the stalest shows.",
        )
        parser.add_argument(
            "--discover",
            action="store_true",
            help="Also sweep Discover for shows now above the vote floor.",
        )
        parser.add_argument("--pages", type=int, default=3, help="Discover pages to sweep.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be refreshed without calling TMDb.",
        )
        parser.add_argument(
            "--skip-rebuild",
            action="store_true",
            help="Do not rebuild the Layer 1 graph afterwards.",
        )

    def handle(self, *args, **options):
        oldest = options["oldest"]
        discover = options["discover"]
        dry_run = options["dry_run"]

        if not oldest and not discover:
            self.stdout.write(self.style.WARNING("Nothing to do. Pass --oldest N or --discover."))
            return

        ingestor = Ingestor()
        touched = False

        if oldest:
            stale = list(Show.objects.order_by("updated_at")[:oldest])
            now = timezone.now()
            self.stdout.write(f"Stalest {len(stale)} of {Show.objects.count()} shows:")
            for s in stale[:5]:
                self.stdout.write(f"  {(now - s.updated_at).days:>4}d  {s.name[:52]}")
            if len(stale) > 5:
                self.stdout.write(f"  ... and {len(stale) - 5} more")

            if not dry_run:
                fell = []
                for s in stale:
                    before = s.vote_count or 0
                    ingestor.ingest_show(s.tmdb_id)
                    s.refresh_from_db()
                    if before >= MIN_VOTE_COUNT and (s.vote_count or 0) < MIN_VOTE_COUNT:
                        fell.append(s)
                touched = True
                self.stdout.write(self.style.SUCCESS(f"Refreshed {len(stale)} shows."))
                if fell:
                    self.stdout.write(
                        self.style.WARNING(
                            f"{len(fell)} fell below vote_count {MIN_VOTE_COUNT}. "
                            "Not removed; run prune_low_signal if you mean it:"
                        )
                    )
                    for s in fell:
                        self.stdout.write(f"  {s.vote_count:>6}  {s.name[:52]}")

        if discover:
            known = set(Show.objects.values_list("tmdb_id", flat=True))
            self.stdout.write(f"\nSweeping {options['pages']} Discover pages ...")
            if dry_run:
                self.stdout.write("  (dry run, no calls made)")
            else:
                before = len(known)
                ingestor.ingest_discover(pages=options["pages"])
                added = Show.objects.exclude(tmdb_id__in=known)
                touched = True
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Catalog {before} -> {Show.objects.count()} "
                        f"({added.count()} new above the floor)."
                    )
                )
                for s in added[:10]:
                    self.stdout.write(f"  + {s.name[:52]}")

        if touched and not options["skip_rebuild"]:
            self.stdout.write("\nRebuilding the Layer 1 graph (ADR-07) ...")
            call_command("rebuild_similar_shows")
