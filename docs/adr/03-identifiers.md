---
adr: 3
title: "Three identities: surrogate pk, tmdb_id, slug"
status: accepted
date: 2026-08-11
tags:
  - adr
relates: []
---
# 3. Three identities: surrogate pk, tmdb_id, slug

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

## After Action Review
Using the identifier TMDb already assigns, rather than minting one, is what keeps the graph
whole. Every show matches on that stable id, so a re-import updates the show already there
instead of creating a second copy, and the connections between shows never split across two
records of the same title.
