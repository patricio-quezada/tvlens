"""
Ingest the shows readers asked for from search (ShowRequest).

Run nightly by the refresh-catalog GitHub Action, before refresh_catalog.
Each requested TMDb id is ingested once, oldest request first, however many
readers asked for it. A request is done once its show exists, so there is no
status to update on success.

A request is marked failed, and never retried, only when TMDb gives a final
answer: a 404, or a daily soap or news bulletin. MIN_VOTE_COUNT is the floor
for bulk ingest, not for requests (Patricio's call, 2026-10-18): a reader asking for
a show by name is the signal the floor stands in for, and Halo: Nightfall has
97 votes. What the floor was really keeping out are the shows under it with
thousands of episodes, which take minutes to ingest and connect to nothing, so
only those (over MAX_LOW_VOTE_EPISODES) are refused. A timeout or a 5xx is not
an answer, so the request just waits for the next night. One show raising an
error never stops the rest. prune_low_signal spares requested shows.

Usage:
    python manage.py ingest_requests
    python manage.py ingest_requests --limit 5 --skip-rebuild
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from shows.ingestion import MIN_VOTE_COUNT, Ingestor
from shows.models import Show, ShowRequest

# A requested show under the bulk floor is refused only when it is this long:
# the daily soaps and news bulletins the floor was built to keep out.
MAX_LOW_VOTE_EPISODES = 1000


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
        client = ingestor.client
        added = 0
        for tmdb_id in pending:
            try:
                resp = client.session.get(f"{client.base_url}/tv/{tmdb_id}", timeout=15)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"TMDb unreachable for {tmdb_id}: {exc}"))
                continue
            if resp.status_code == 404:
                self.fail(tmdb_id, "TMDb has no such show")
                continue
            if not resp.ok:
                self.stdout.write(self.style.WARNING(f"TMDb {resp.status_code} for {tmdb_id}"))
                continue
            data = resp.json()
            votes = data.get("vote_count") or 0
            episodes = data.get("number_of_episodes") or 0
            if votes < MIN_VOTE_COUNT and episodes > MAX_LOW_VOTE_EPISODES:
                self.fail(
                    tmdb_id,
                    f"{episodes} episodes on {votes} votes: a soap or bulletin, not added",
                )
                continue
            try:
                show = ingestor.ingest_show(tmdb_id)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Ingest failed for {tmdb_id}: {exc}"))
                continue
            if show:
                added += 1
                self.stdout.write(self.style.SUCCESS(f"Ingested {show.name} ({tmdb_id})"))

        if added and not options["skip_rebuild"]:
            self.stdout.write("\nRebuilding the Layer 1 graph (ADR-07) ...")
            call_command("rebuild_similar_shows")

    def fail(self, tmdb_id, reason):
        ShowRequest.objects.filter(tmdb_id=tmdb_id).update(failed=True)
        self.stdout.write(self.style.WARNING(f"Not adding {tmdb_id}: {reason}"))
