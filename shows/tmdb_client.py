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
                    time.sleep(2 ** attempt)
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

    def search_tv(self, query, page=1):
        """Search TV shows by name."""
        return self._get("/search/tv", params={"query": query, "page": page})

    def get_tv_details(self, tv_id, append_to_response="credits"):
        """Full details for a single show including credits."""
        return self._get(
            f"/tv/{tv_id}",
            params={"append_to_response": append_to_response},
        )

    def get_tv_season(self, tv_id, season_number):
        """Season details with episode list."""
        return self._get(f"/tv/{tv_id}/season/{season_number}")

    def get_person(self, person_id):
        """Person bio details."""
        return self._get(f"/person/{person_id}")

    def get_tv_recommendations(self, tv_id, page=1):
        """TMDB-generated recommendations for a show."""
        return self._get(f"/tv/{tv_id}/recommendations", params={"page": page})

    def get_trending_tv(self, time_window="week"):
        """Trending TV shows (day or week)."""
        return self._get(f"/trending/tv/{time_window}")
