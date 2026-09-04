---
adr: 3
title: "Three identities: pk, tmdb_id, slug"
status: accepted
date: 2026-08-11
tags:
  - adr
relates: []
---
# 3. Three identities: pk, tmdb_id, slug

**A show is identified three times, for three different jobs: an integer pk for foreign keys, a
tmdb_id for matching on ingest, and a slug for URLs. One clever identifier serving all three
would tie the database's spine to an external service's ID.**

## Context
A show needs identifying in three places: internally for foreign keys, on ingest for matching
against TMDb, and publicly in URLs.

The tempting move is one nice identifier doing all three. Something branded, like
`tvl-{tmdb_id}`, reads well in a URL and matches TMDb for free.

## Decision
Keep three identifiers, each with one job.

- **`pk`, an auto-increment integer,** is the internal spine, the foreign-key target for every
  other table. Its durability comes from meaning nothing. A pk only forces a painful cascade
  when it has to *change*, and only meaningful keys ever have to change. That is why the
  `tvl-{tmdb_id}` scheme lost: it decorates an external ID while hiding the dependency, and it
  hands TMDb's occasional ID merges the power to force pk rewrites across the database.
- **`tmdb_id`, unique,** is the ingest identity. `unique=True` plus tmdb-keyed upserts make
  imports idempotent, which is where sync safety actually lives.
- **`slug`** is the public identity in URLs, `/shows/breaking-bad/`. Chosen over a raw numeric id
  for readability, and because slugs survive a database rebuild while pks do not. It arrived with
  the show detail page.

## Amendment, 2026-08-30: the surrogate pk IS the tmdb_id for Show

The three identities stand, but for Show two of them are now the same number.
`Show.id` was a `BigAutoField`, which records the order rows happened to be
inserted on one machine and means nothing on any other. `tmdb_id` means the
same thing everywhere, forever, and was already unique and indexed.

That difference is harmless while TVLens is one deployment. It stops being
harmless the moment a catalog is shipped into a database that already holds
ratings: a fresh ingest assigns different surrogate ids, and every rating
silently re-points at a different show. Not an error, not a crash. A 5.0 on
The Wire quietly becomes a 5.0 on something else, and ratings are the one
thing in TVLens that cannot be regenerated. See issue #28.

Done as a data operation rather than a schema migration: no ALTER, no column
change. `match_show_ids` rewrites the values in two phases, because the ranges
overlapped, on this catalog at 8 values that were both an existing id and some
show's tmdb_id. A single pass would have collided mid-flight.

The check that mattered ran before and after: every rating had to resolve to
the same TMDB ID it began with, or nothing committed. 248 shows rewritten, 488
ratings unchanged, 11 tables followed.

Eleven, not the nine the models declare. The two many-to-many join tables,
`shows_show_genres` and `shows_show_networks`, have no model of their own and
were missed by a hand-written list. The integrity check caught it and rolled
back; the command now reads the foreign keys from the schema, which is the
version that cannot be incomplete.

**The invariant this creates:** every Show must be created with an explicit
`id=tmdb_id`. `Ingestor.ingest_show` does it, and `ShowIdentityTests` fails if
any show in the catalog drifts. Without it an autofield hands out a low integer
that collides with a real tmdb_id.

## After Action Review
Using the identifier TMDb already assigns, rather than minting one, is what keeps the graph
whole. Every show matches on that stable id, so a re-import updates the show already there
instead of creating a second copy, and the connections between shows never split across two
records of the same title.
