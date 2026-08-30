"""
Thin wrapper around the TMDB v3 REST API.

Usage:
    from shows.tmdb_client import TMDBClient
    client = TMDBClient()
    results = client.discover_tv(page=1)
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class TMDBClient:
    """Handles all HTTP communication with the TMDB API."""

    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key or settings.TMDB_API_KEY
        self.base_url = base_url or settings.TMDB_BASE_URL
        self.session = requests.Session()
        self.session.params = {"api_key": self.api_key}

    # ── helpers ───────────────────────────────────────────────────────────

    def _get(self, endpoint, params=None, retries=3):
        """GET with simple retry + back-off."""
        url = f"{self.base_url}{endpoint}"
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2))
                    logger.warning("TMDB rate-limited, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                logger.error("TMDB request failed (attempt %d): %s", attempt + 1, exc)
                if attempt < retries - 1:
                    time.sleep(2**attempt)
        return None

    # ── public API ────────────────────────────────────────────────────────

    def get_tv_genres(self):
        """Return list of TV genre dicts."""
        data = self._get("/genre/tv/list")
        return data.get("genres", []) if data else []

    def discover_tv(self, page=1, sort_by="popularity.desc", **filters):
        """Discover TV shows with optional filters."""
        params = {"page": page, "sort_by": sort_by, **filters}
        return self._get("/discover/tv", params=params)

    def get_tv_details(self, tv_id, append_to_response="credits,videos"):
        """Full details for a single show including credits."""
        return self._get(
            f"/tv/{tv_id}",
            params={"append_to_response": append_to_response},
        )

    def get_tv_aggregate_credits(self, tv_id):
        """Credits rolled up across every episode, with per-role episode counts.

        Differs from the `credits` append on get_tv_details, which returns only
        series-level billing. Cast entries carry `roles: [{character,
        episode_count}]` and crew carry `jobs: [{job, episode_count}]`.
        """
        return self._get(f"/tv/{tv_id}/aggregate_credits")

    def get_tv_season(self, tv_id, season_number):
        """Season details with episode list."""
        return self._get(f"/tv/{tv_id}/season/{season_number}")


# Four endpoints were removed on 2026-08-30, all unused and two of them
# contrary to decisions already made: /tv/{id}/recommendations returns TMDb's
# own recommendations, which is the thing TVLens exists not to serve, and
# /trending returns popularity, which ADR-05 forbids as a ranking. /search/tv
# went because ADR-12 builds catalog search locally, and /person because
# nothing asks for a bio. An unused client method next to a rule it breaks is
# an invitation; add them back deliberately if a decision ever calls for them.
