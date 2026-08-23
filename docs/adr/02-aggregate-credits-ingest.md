# 2. Ingest aggregate_credits, and record episode counts

## Context
The recommender read `/tv/{id}?append_to_response=credits`, which returns only **series-level**
billing, a short curated list. It cannot see per-episode credits. The clearest symptom: TMDb
credits television directors per episode, so the whole 100-show catalog held only 13 director
rows, and not one of them connected two shows. Breaking Bad returned 8 cast from this endpoint;
TMDb's `aggregate_credits` returns 348, plus 25 directors.

## Decision
- Add **`episode_count`** (nullable) to `CastMember` and `CrewMember`. Null means a
  series-level credit with no episode rollup, which is real signal, not missing data.
- Add `TMDBClient.get_tv_aggregate_credits()` and ingest it **after** the series
  credits, so aggregate fills counts on rows the series pass created.
- **Keep both endpoints.** Series credits hold authorship credits (Creator,
  Characters, Novel) that never attach to an episode; aggregate holds who did the
  labour, with counts. Different signals, both wanted.
- **Remove the `[:25]` cast cap** in ingestion. It silently truncated billing and
  would cap 348 cast at 25.
- Backfill via a `--show <tmdb_id>`-scoped management command that fetches only
  aggregate_credits (not seasons/episodes), so a catalog pass is one API call per show.

## After Action Review
Expanding the ingest grew the data enormously. Cast credits went from about 700 to over
128,000, crew from about 1,700 to 24,000, and recorded directors from 13 to more than
3,000.

But more data made the recommender worse, not better. Most of those new credits are
single-episode appearances, so counting everyone equally buried the real matches under
guest actors that two shows happened to share. This is what forced the episode weighting
in [ADR-04](04-episode-weighted-people-recommender.md): the deeper data and the
weighting had to ship together, because the deeper data on its own made the product
worse.
