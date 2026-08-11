# 3. Three identities: surrogate pk, tmdb_id, slug

Status: Accepted
Date: 2026-08-07

## Context
A show needs to be identified in three different places: internally (for foreign
keys), on ingest (matching TMDb), and in public URLs. It is tempting to make one
"nice" identifier serve all three (e.g. a branded `tvl-{tmdb_id}`).

## Decision
Keep three separate identifiers, each with one job:

- **`pk` (auto-increment integer)** stays the internal spine. It is the foreign-key
  target for all other tables and its durability comes from it **meaning nothing**.
  A pk only forces a painful cascade when it has to *change*, and only meaningful
  keys ever have to change. So no branded or custom pk. A `tvl-{tmdb_id}` scheme was
  considered and rejected: it decorates an external ID while hiding the dependency,
  and it hands TMDb's occasional ID merges the power to force pk rewrites.
- **`tmdb_id` (unique)** is the ingest identity. `unique=True` plus tmdb-keyed
  upserts make imports idempotent, which is where sync-safety actually lives.
- **`slug`** is the public identity in URLs (`/shows/breaking-bad/`), chosen over a
  raw numeric id for readability and because slugs survive a database rebuild while
  pks do not. Added when the show detail page is built.

## Consequences
- Consistency is enforced by the uniqueness constraint, not by encoding meaning into
  a key. The meaningless pk is the thing nothing external can force us to change.
- The model already carries pk and tmdb_id; slug is a small addition at detail-page
  time.

Decided in Linear (QUE-5 comment), migrated here 2026-08-11.
