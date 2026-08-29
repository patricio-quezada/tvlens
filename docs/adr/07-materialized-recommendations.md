---
adr: 7
title: Materialize the Layer 1 ranking and serve reads from a table
status: accepted
date: 2026-08-17
tags:
  - adr
relates:
  - "[[05-no-signal-fallback-ladder]]"
  - "[[06-sql-variable-ceiling]]"
---
# 7. Materialize the Layer 1 ranking and serve reads from a table

**The shared-people ranking is the same for every visitor and only changes when an ingest
changes the credits underneath it, so TVLens computes it once into a table and reads from
there. Ingest owns the refresh, which means the store can never go stale.**

## Context
Open a show page and TVLens works out its "more shows like this" list from scratch. It reads
every person in the cast and crew, pulls the credits of every show they touched, scores each
candidate by episode share, and ranks them.

Then the next visitor opens the same page, and it does all of it again.

The list does not depend on who is looking. It only changes when an ingest changes the credits
underneath it. So every page load after the first was paying full price for an answer TVLens
already had -- and [ADR-06](06-sql-variable-ceiling.md) had already named it as the open
follow-up, issue #1, problem 2: the pages run the recommenders on every request with no
caching.

## Decision
Precompute the whole Layer 1 ranking once and store it, then serve reads from the store.

**A new table, `SimilarShow`, holds the graph.** One row is one edge: a `source` show, a
`target` show, the `rank` (0-based position in the source's list), the `score`, the
`shared_people` count, and the `mode`. Rebuilding is wholesale: a management command,
`rebuild_similar_shows`, deletes every edge, calls `similar_by_people` for every show, and
bulk-inserts the results inside one transaction, so a reader sees either the old graph or the
new one and never a half-built mix. The read path, `stored_similar(show)`, reads that source's
rows in rank order and returns the same `RankedShows` shape the live function returned, so the
views and templates downstream do not change.

**Every edge carries the source's `mode`.** `mode` is the source's rung on the fallback ladder
([ADR-05](05-no-signal-fallback-ladder.md)): weighted, estimated, or rating. It belongs to the
source, not the edge, so every one of a source's edges carries the same value. Storing it per
edge repeats one small string, but it keeps the whole graph in one table with no second lookup,
which is the simplest thing that round-trips the `RankedShows` return value. A separate per-
source table would normalize away the repeat and buy nothing at catalog scale.

**The invalidation is the rebuild.** There is no per-row cache expiry and no read-through
fallback. A stale store is impossible because ingest owns the refresh: both `ingest_shows` and
`backfill_aggregate_credits` call `rebuild_similar_shows` once at the end of their run, after
the batch is written. Backfilling episode counts changes every weighted score, so the rebuild
has to follow it; a new show changes the graph, so it follows that too. Between ingests the
catalog does not move, so the store cannot drift.

**The store holds the global graph only; the per-show callout prose stays live.** The detail
page still composes each recommendation's sentence live (`role_index`, `shared_connections`,
`compose_callout`). That is deliberate. The ranking is global and stable, so it stores well.
The callout is on its way to becoming personalized (issue #7): the same edge will read
differently for different viewers, so its text is not a global fact and does not belong in a
global table. The rule is the split itself: store the part of the answer that is the same for
everyone, keep the part that depends on the viewer live.

**`similar_by_cast` and `similar_by_crew` stay live.** They are single annotated subqueries
([ADR-06](06-sql-variable-ceiling.md)), cheap and not the scale risk, so materializing them
would add a table and an invalidation path for no gain. Only the Python-scored people graph,
the expensive one, moves into the store.

I considered a lazy read-through cache: compute on first request, cache the result, serve the
cache until the next ingest busts it. I rejected it. It leaves the first request after every
ingest paying the full cost, it needs a keying and expiry scheme the wholesale rebuild does
not, and it hides the graph inside a cache rather than making it a queryable table that later
layers and debugging can read directly. Precomputing everything up front is both simpler to
reason about and uniformly fast to read.

## Amendment, 2026-08-29: the cast/crew split rides on the edge

Two columns join `score`, `shared_people`, and `mode`: `cast_contribution` and
`crew_contribution`, the score split by what the shared people were doing on
each side.

They are here for exactly the reason the rest of the row is. Layer 2's
connection-type preference (ADR-15) needs the split per edge, and computing it
at request time meant a `role_indexes` pass over every show the reader had
rated. Profiled on the real catalog that was **53% of the entire profile
build**, spent re-deriving numbers that only change on ingest.

Moving it to the edge cut a 249-rating profile from 579ms to 205ms, and a
profile that has rated **every show in the catalog** now builds in 224ms with
22 queries. It also removed the ceiling on how many edges Layer 2 may read,
which is what made ADR-15's estimator work at all.

The cost is two floats per edge, written by `rebuild_similar_shows` in the same
pass that already computes the ranking, and one full rebuild to backfill.

## After Action Review
The store holds the same graph the live recommender produced. Rebuilt against the real 100-show
catalog it writes 1041 edges across 96 sources that have at least one similar show (4 shows
share no one). A full comparison of `stored_similar` against a pre-rebuild snapshot of
`similar_by_people` matches row for row across all 100 sources, zero mismatches, and Breaking
Bad's weighted preview is unchanged: Better Call Saul 14.79, The Blacklist 1.09, CSI 0.19 in
eighth. Tests freeze the invariant:

- the store equals the live computation for representative sources
- an edgeless source stores nothing and reads back empty
- a non-weighted (estimated) mode round-trips
- the detail view renders from the stored edges
- a rebuild wipes stale edges

Still open, and out of scope for v1: the rebuild is wholesale. It recomputes every show even
when an ingest touched a handful, which is fine at catalog scale (a full rebuild is cheap on
100 shows) but does not scale to a large catalog. The future optimization is an incremental
rebuild that recomputes only the shows an ingest actually affected, plus the shows that link to
them. That is a real piece of work, deliberately not attempted here.

Provenance: issue #1 (Recommender scale hardening), problem 2, the caching follow-up named in
[ADR-06](06-sql-variable-ceiling.md).
## Note, 2026-08-26: the review above is a measurement, not an invariant
**status: draft**

The After Action Review records "Better Call Saul 14.79, The Blacklist 1.09, CSI 0.19 in eighth"
as Breaking Bad's preview, along with 1041 edges across 96 sources. Those numbers were true of
the 100-show catalog they were measured on. They are left as written, because an After Action
Review is a record of what was found at the time and rewriting it would destroy that.

They are no longer true, and one of them stopped being true long before anyone noticed. **CSI:
Crime Scene Investigation left Breaking Bad's top 12 entirely as the catalog grew past 100
shows.** It is still in the catalog. Nothing failed, because a claim in prose cannot fail, and
the sentence sat here being wrong for months.

On the 464-show catalog, after the rescoring in
[ADR-04](04-episode-weighted-people-recommender.md) and the widened `SERVICE_JOBS` in
[ADR-01](01-exclude-casting-roles.md), the store holds **4,013 edges across 409 sources**, and
Breaking Bad reads:

| # | show | score |
|---|---|---|
| 1 | Better Call Saul | 9.7473 |
| 2 | The Blacklist | 1.0204 |
| 3 | Malcolm in the Middle | 1.0133 |
| 4 | Westworld | 0.3775 |
| 5 | The Mandalorian | 0.3641 |
| 6 | The Boys | 0.3584 |
| 7 | House of the Dragon | 0.3552 |
| 8 | Fargo | 0.3418 |

**The real fix is that these facts now live somewhere that can fail.** `python manage.py
check_ground_truth` asserts them against the live store and exits non-zero when they break. Run
it after every rebuild. It is a management command rather than a unit test on purpose: the test
database is empty, and recreating Breaking Bad's 75 shared people as a fixture would freeze a
copy of the data rather than the data, which is exactly the failure this note is about.

Numbers quoted in this file, or in `docs/artifacts/design-kit/03-sample-data.md`, should be
treated as dated measurements. The command is the live record.
