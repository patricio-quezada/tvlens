---
adr: 13
title: "Resolve seasons and episodes by two keys, because TMDb keeps neither stable"
status: accepted
date: 2026-08-24
tags:
  - adr
relates:
  - "[[03-identifiers]]"
  - "[[02-aggregate-credits-ingest]]"
---
# 13. Resolve seasons and episodes by two keys, because TMDb keeps neither stable

**Season and Episode each carry a unique `tmdb_id` and a unique natural key, and TMDb honors
neither across time. Upserts on those two tables now resolve by id first, fall back to the natural
key, and evict whatever stale row is sitting on the key they are about to claim. This amends
[ADR-03](03-identifiers.md), which says `tmdb_id` is the ingest identity. For a show that is still
true. For its children it is not.**

## Context
Growing the catalog from 100 shows to 500 meant re-ingesting the shows already present. That had
never been done. Every previous ingest ran against a fresh database, so every upsert was an insert
and no key was ever tested for stability.

Forty-five seconds into the run:

```
IntegrityError: UNIQUE constraint failed: shows_season.show_id, shows_season.season_number
```

`Season` carries two unique keys: its own `tmdb_id`, and the pair `(show, season_number)`. The
upsert keyed on `tmdb_id` alone. TMDb had reassigned a season's id, so the lookup missed, and the
insert that followed collided with the season number already on file.

Keying on the natural key instead produced the mirror failure:

```
IntegrityError: UNIQUE constraint failed: shows_episode.tmdb_id
```

TMDb moves an episode between seasons while keeping its id. The natural key misses, and the insert
collides on `tmdb_id`.

**Neither key alone is sufficient, and the two failures are symmetric.** Choosing either one leaves
the other class of drift unhandled, and both fail as an IntegrityError partway through an ingest
that has already written thousands of rows.

This is not a TMDb defect. A catalog that tracks decades of television has to reorganize
seasons, split specials, and move episodes when a broadcaster renumbers a run. The identifiers
move because the underlying facts move.

## Decision
One helper, `Ingestor._upsert_child`, resolves both tables:

1. Look the row up by `tmdb_id`.
2. If that misses, fall back to the natural key.
3. If the id matched but a *different* row occupies the natural key, delete that row. TMDb no
   longer recognizes it, and leaving it would block the write forever.
4. Write both keys and every field.

Step three is the uncomfortable one and it is deliberate. The evicted row is a record TMDb has
stopped acknowledging, and its only remaining function is to make the constraint fail. Nothing user
generated hangs off a Season or an Episode, so the eviction costs metadata that the same ingest is
about to rewrite.

**ADR-03 is amended, not overturned.** Its claim holds for `Show`, where `tmdb_id` is genuinely
stable, because TMDb does not merge or renumber whole series the way it reorganizes their parts.
The claim was written when only `Show` had been exercised against a real ingest.

## After Action Review
The bug was invisible for twelve days because a catalog built once in a single pass never
re-resolves anything. **Idempotency is not tested by running an importer; it is tested by running
it twice.** Any future importer in this project should be run twice against a populated database
before it is called finished.

The fix carried a second benefit that was not designed for. `trailer_key` was populated on 4 shows
of 100, because trailers shipped on Day 21 and only four shows had been ingested after that.
Re-ingest runs `_pick_trailer` on every show, so the count went to 223 of 464 without a backfill
command being written.

The catalog after the run: 464 shows, 6,194 seasons, 486,829 episodes, 278,632 cast rows.
