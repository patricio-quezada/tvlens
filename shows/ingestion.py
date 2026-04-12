"""
Data ingestion pipeline — pulls TV show data from TMDB and upserts into Django models.

Usage:
    from shows.ingestion import Ingestor
    ingestor = Ingestor()
    ingestor.ingest_discover(pages=5)
"""

import logging

from django.db import transaction

from .models import (
    CastMember,
    CrewMember,
    Episode,
    Genre,
    Network,
    Person,
    Season,
    Show,
)
from .tmdb_client import TMDBClient

logger = logging.getLogger(__name__)


class Ingestor:
    def __init__(self, client=None):
        self.client = client or TMDBClient()

    # ── genres ────────────────────────────────────────────────────────────

    def sync_genres(self):
        genres = self.client.get_tv_genres()
        for g in genres:
            Genre.objects.update_or_create(
                tmdb_id=g["id"],
                defaults={"name": g["name"]},
            )
        logger.info("Synced %d genres", len(genres))

    # ── discover / bulk ───────────────────────────────────────────────────

    def ingest_discover(self, pages=5, sort_by="popularity.desc"):
        """Pull shows from TMDB Discover endpoint, then hydrate details."""
        self.sync_genres()
        show_ids = []
        for page in range(1, pages + 1):
            data = self.client.discover_tv(page=page, sort_by=sort_by)
            if not data:
                break
            for item in data.get("results", []):
                show_ids.append(item["id"])
        logger.info("Discovered %d show IDs across %d pages", len(show_ids), pages)
        for tmdb_id in show_ids:
            self.ingest_show(tmdb_id)

    # ── single show ───────────────────────────────────────────────────────

    @transaction.atomic
    def ingest_show(self, tmdb_id):
        """Fetch full details for one show and upsert everything."""
        data = self.client.get_tv_details(tmdb_id)
        if not data:
            logger.warning("No data for show %s", tmdb_id)
            return None

        show, _ = Show.objects.update_or_create(
            tmdb_id=tmdb_id,
            defaults={
                "name": data.get("name", ""),
                "original_name": data.get("original_name", ""),
                "overview": data.get("overview", ""),
                "tagline": data.get("tagline", ""),
                "poster_path": data.get("poster_path", "") or "",
                "backdrop_path": data.get("backdrop_path", "") or "",
                "first_air_date": data.get("first_air_date") or None,
                "last_air_date": data.get("last_air_date") or None,
                "status": data.get("status", ""),
                "vote_average": data.get("vote_average", 0),
                "vote_count": data.get("vote_count", 0),
                "popularity": data.get("popularity", 0),
                "original_language": data.get("original_language", ""),
                "number_of_seasons": data.get("number_of_seasons", 0),
                "number_of_episodes": data.get("number_of_episodes", 0),
                "homepage": data.get("homepage", "") or "",
            },
        )

        # Genres
        genre_ids = [g["id"] for g in data.get("genres", [])]
        genres = Genre.objects.filter(tmdb_id__in=genre_ids)
        show.genres.set(genres)

        # Networks
        for net in data.get("networks", []):
            network, _ = Network.objects.update_or_create(
                tmdb_id=net["id"],
                defaults={
                    "name": net.get("name", ""),
                    "logo_path": net.get("logo_path", "") or "",
                    "origin_country": net.get("origin_country", ""),
                },
            )
            show.networks.add(network)

        # Credits
        credits = data.get("credits", {})
        self._upsert_cast(show, credits.get("cast", []))
        self._upsert_crew(show, credits.get("crew", []))

        # Seasons & episodes
        for s in data.get("seasons", []):
            self._upsert_season(show, s)

        logger.info("Ingested show: %s (tmdb_id=%s)", show.name, tmdb_id)
        return show

    # ── private helpers ───────────────────────────────────────────────────

    def _upsert_cast(self, show, cast_list):
        for entry in cast_list[:25]:  # top 25 billed
            person = self._get_or_create_person(entry)
            CastMember.objects.update_or_create(
                show=show,
                person=person,
                character=entry.get("character", ""),
                defaults={"order": entry.get("order", 0)},
            )

    def _upsert_crew(self, show, crew_list):
        seen = set()
        for entry in crew_list:
            key = (entry["id"], entry.get("job", ""))
            if key in seen:
                continue
            seen.add(key)
            person = self._get_or_create_person(entry)
            CrewMember.objects.update_or_create(
                show=show,
                person=person,
                job=entry.get("job", ""),
                defaults={"department": entry.get("department", "")},
            )

    def _get_or_create_person(self, entry):
        person, _ = Person.objects.update_or_create(
            tmdb_id=entry["id"],
            defaults={
                "name": entry.get("name", ""),
                "profile_path": entry.get("profile_path", "") or "",
                "known_for_department": entry.get("known_for_department", ""),
            },
        )
        return person

    def _upsert_season(self, show, season_data):
        if not season_data.get("id"):
            return
        season, _ = Season.objects.update_or_create(
            tmdb_id=season_data["id"],
            defaults={
                "show": show,
                "season_number": season_data.get("season_number", 0),
                "name": season_data.get("name", ""),
                "overview": season_data.get("overview", ""),
                "poster_path": season_data.get("poster_path", "") or "",
                "air_date": season_data.get("air_date") or None,
                "episode_count": season_data.get("episode_count", 0),
            },
        )

        # Fetch full episode details for this season
        ep_data = self.client.get_tv_season(show.tmdb_id, season.season_number)
        if not ep_data:
            return
        for ep in ep_data.get("episodes", []):
            Episode.objects.update_or_create(
                tmdb_id=ep["id"],
                defaults={
                    "season": season,
                    "episode_number": ep.get("episode_number", 0),
                    "name": ep.get("name", ""),
                    "overview": ep.get("overview", ""),
                    "still_path": ep.get("still_path", "") or "",
                    "air_date": ep.get("air_date") or None,
                    "vote_average": ep.get("vote_average", 0),
                    "vote_count": ep.get("vote_count", 0),
                    "runtime": ep.get("runtime"),
                },
            )
