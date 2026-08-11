# 4. Ingest aggregate_credits, and record episode counts

Status: Accepted
Date: 2026-08-06

## Context
The recommender read `/tv/{id}?append_to_response=credits`, which returns only
**series-level** billing, a short curated list. It cannot see per-episode credits.
The clearest symptom: television directors are credited per episode, so the whole
100-show catalog held 13 director rows and not one created an edge. Breaking Bad
returned 8 cast from this endpoint; TMDb's `aggregate_credits` returns 348, plus 25
directors.

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

## Consequences
- Cast rows 728 → ~128k, crew 1,665 → ~24k, Director rows 13 → 3,270, directors on
  2+ shows 0 → 480 (Félix Alcalá on Breaking Bad and CSI; John Dahl on Breaking Bad,
  Dexter, Outlander).
- **This made the recommender worse on its own**, 84% of cast rows are single-episode,
  so equal counting became guest-actor churn. That is what forced ADR-0001 (weighting);
  the ingest and the weighting had to land together.

Decided in Linear (QUE-8), migrated here 2026-08-11.
