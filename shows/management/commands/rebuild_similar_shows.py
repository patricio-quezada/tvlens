"""
Rebuild the materialized Layer 1 graph in the SimilarShow table.

similar_by_people(show) is a pure function of the catalog, so its whole output
is precomputed here and served from the table on every request. The rebuild is
wholesale: delete every edge, recompute every source, write the new edges in one
transaction. That is the invalidation strategy too, so ingest just calls this at
the end of its run. See docs/adr/07-materialized-recommendations.md.

Usage:
    python manage.py rebuild_similar_shows
    python manage.py rebuild_similar_shows --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from shows.models import Show, SimilarShow
from shows.recommenders import (
    connection_type,
    role_indexes,
    shared_connections,
    similar_by_people,
)


class Command(BaseCommand):
    help = "Recompute the SimilarShow store from the live people graph"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the edge count without writing anything",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # One transaction so a reader never sees a half-rebuilt store: either the
        # old graph or the new one, never a mix. The delete-then-recompute pass
        # is cheap at catalog scale (100 shows); an incremental rebuild of only
        # the shows an ingest touched is a future optimization, out of scope for
        # v1 (ADR-07).
        with transaction.atomic():
            existing = SimilarShow.objects.count()
            if not dry_run:
                SimilarShow.objects.all().delete()

            # The cast/crew split is computed here, once, for the same reason
            # the score is: Layer 2 needs it per edge and recomputing it at
            # request time cost a role_indexes pass over every rated show
            # (ADR-15). One index over the whole catalog serves every edge.
            all_shows = list(Show.objects.all())
            indexes = role_indexes(all_shows)
            by_id = {s.id: s for s in all_shows}

            edges = []
            for show in all_shows:
                ranked = similar_by_people(show)
                for rank, target in enumerate(ranked):
                    mass = {"cast": 0.0, "crew": 0.0}
                    other = by_id.get(target.id)
                    if other is not None:
                        for c in shared_connections(
                            show, indexes[show.id], other, indexes[other.id]
                        ):
                            mass[connection_type(c.kind)] += c.contribution
                    edges.append(
                        SimilarShow(
                            source=show,
                            target=target,
                            rank=rank,
                            score=target.score,
                            shared_people=target.shared_people,
                            cast_contribution=mass["cast"],
                            crew_contribution=mass["crew"],
                            mode=ranked.mode,
                        )
                    )

            if dry_run:
                self.stdout.write(f"Would replace {existing} edges with {len(edges)}.")
                # Nothing is committed on a dry run.
                transaction.set_rollback(True)
                return

            SimilarShow.objects.bulk_create(edges)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt SimilarShow: {len(edges)} edges across {Show.objects.count()} shows."
            )
        )
