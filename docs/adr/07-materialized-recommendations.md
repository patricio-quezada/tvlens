# 7. Materialize the Layer 1 ranking and serve reads from a table

## Context
Every show page runs `similar_by_people(show)` to build its "more shows like this" list.
That function is a pure function of the catalog graph: it reads cast and crew, scores each
candidate by episode share, and returns a ranked list. The same show yields the same list
for everyone, and the list only changes when an ingest changes the underlying credits.

Yet the work happens on every request. Each page load fetches the source show's people,
chunks them under SQLite's variable ceiling ([ADR-06](06-sql-variable-ceiling.md)), pulls
every candidate's credits, and scores them all in Python. The scores are correct; the cost
is that a computation whose answer does not depend on the viewer, and does not change
between ingests, is redone from scratch for each anonymous visitor. ADR-06 named this as
the open follow-up (issue #1, problem 2): the pages run the recommenders on every request
with no caching.

## Decision
Precompute the whole Layer 1 ranking once and store it, then serve reads from the store.

**A new table, `SimilarShow`, holds the graph.** One row is one edge: a `source` show, a
`target` show, the `rank` (0-based position in the source's list), the `score`, the
`shared_people` count, and the `mode`. Rebuilding is wholesale: a management command,
`rebuild_similar_shows`, deletes every edge, calls `similar_by_people` for every show, and
bulk-inserts the results inside one transaction, so a reader sees either the old graph or
the new one and never a half-built mix. The read path, `stored_similar(show)`, reads that
source's rows in rank order and returns the same `RankedShows` shape the live function
returned, so the views and templates downstream do not change.

**The `mode` is denormalized onto every edge.** `mode` is the source's rung on the
fallback ladder ([ADR-05](05-no-signal-fallback-ladder.md)): weighted, estimated, or
rating. It belongs to the source, not the edge, so every one of a source's edges carries
the same value. Storing it per edge repeats one small string, but it keeps the whole graph
in one table with no second lookup, which is the simplest thing that round-trips the
`RankedShows` return value. A separate per-source table would normalize away the repeat and
buy nothing at catalog scale.

**The invalidation is the rebuild.** There is no per-row cache expiry and no read-through
fallback. A stale store is impossible because ingest owns the refresh: both `ingest_shows`
and `backfill_aggregate_credits` call `rebuild_similar_shows` once at the end of their run,
after the batch is written. Backfilling episode counts changes every weighted score, so the
rebuild has to follow it; a new show changes the graph, so it follows that too. Between
ingests the catalog does not move, so the store cannot drift.

**Only the global graph is materialized; the per-show callout prose is not.** The detail
page still composes each recommendation's sentence live (`role_index`, `shared_connections`,
`compose_callout`). That is deliberate. The ranking is global and stable, so it stores well.
The callout is on its way to becoming personalized (issue #7): the same edge will read
differently for different viewers, so its text is not a global fact and does not belong in a
global table. The rule is the split itself: store the part of the answer that is the same
for everyone, keep the part that depends on the viewer live.

**`similar_by_cast` and `similar_by_crew` stay live.** They are single annotated subqueries
([ADR-06](06-sql-variable-ceiling.md)), cheap and not the scale risk, so materializing them
would add a table and an invalidation path for no gain. Only the Python-scored people graph,
the expensive one, moves into the store.

I considered a lazy read-through cache: compute on first request, cache the result, serve
the cache until the next ingest busts it. I rejected it. It leaves the first request after
every ingest paying the full cost, it needs a keying and expiry scheme the wholesale rebuild
does not, and it hides the graph inside a cache rather than making it a queryable table that
later layers and debugging can read directly. Precomputing everything up front is both
simpler to reason about and uniformly fast to read.

## After Action Review
The store holds the same graph the live recommender produced. Rebuilt against the real
100-show catalog it writes 1041 edges across 96 sources that have at least one similar show
(4 shows share no one). A full comparison of `stored_similar` against a pre-rebuild snapshot
of `similar_by_people` matches row for row across all 100 sources, zero mismatches, and
Breaking Bad's weighted preview is unchanged: Better Call Saul 14.79, The Blacklist 1.09,
CSI 0.19 in eighth. Tests freeze the invariant: the store equals the live computation for
representative sources, an edgeless source stores nothing and reads back empty, a
non-weighted (estimated) mode round-trips, the detail view renders from the stored edges,
and a rebuild wipes stale edges.

Still open, and out of scope for v1: the rebuild is wholesale. It recomputes every show even
when an ingest touched a handful, which is fine at catalog scale (a full rebuild is cheap on
100 shows) but does not scale to a large catalog. The future optimization is an incremental
rebuild that recomputes only the shows an ingest actually affected, plus the shows that link
to them. That is a real piece of work, deliberately not attempted here.

Provenance: issue #1 (Recommender scale hardening), problem 2, the caching follow-up named
in [ADR-06](06-sql-variable-ceiling.md).
