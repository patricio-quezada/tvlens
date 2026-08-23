# 2. Ingest aggregate_credits, and record episode counts

**The credits endpoint TVLens was reading returns only series-level billing, which left the
catalog with 13 directors in total. Ingesting `aggregate_credits` alongside it, with an episode
count on every row, gave the recommender the people it was missing -- and immediately made the
rankings worse, which is what forced the weighting in ADR-04.**

## Context
The whole 100-show catalog held 13 director rows. Not one of them connected two shows.

The cause was the endpoint. TVLens read `/tv/{id}?append_to_response=credits`, which returns
**series-level** billing: a short, curated list. It cannot see per-episode credits at all, and
TMDb credits television directors per episode. So the directors were there on TMDb and
invisible here.

The size of the gap is easier to see on one show. Breaking Bad returned 8 cast members from
that endpoint. TMDb's `aggregate_credits` returns 348, plus 25 directors.

## Decision
- Add **`episode_count`** (nullable) to `CastMember` and `CrewMember`. Null means a series-level
  credit with no episode rollup, which is real signal rather than missing data.
- Add `TMDBClient.get_tv_aggregate_credits()` and ingest it **after** the series credits, so the
  aggregate pass fills counts on rows the series pass created.
- **Keep both endpoints.** Series credits hold authorship credits -- Creator, Characters, Novel
  -- that never attach to an episode. Aggregate holds who did the labour, with counts. Different
  signals, both wanted.
- **Remove the `[:25]` cast cap** in ingestion. It silently truncated billing, and it would have
  capped Breaking Bad's 348 cast at 25.
- Backfill through a `--show <tmdb_id>`-scoped management command that fetches only
  aggregate_credits, not seasons or episodes, so a catalog pass costs one API call per show.

## After Action Review
The data grew enormously. Cast credits went from about 700 to over 128,000, crew from about
1,700 to 24,000, and recorded directors from 13 to more than 3,000.

And the recommender got worse.

Most of those new credits are single-episode appearances, so counting everyone equally buried
the real matches under guest actors that two shows happened to share. Deeper data on its own
made the product worse, which is what forced the episode weighting in
[ADR-04](04-episode-weighted-people-recommender.md). The two had to ship together.
