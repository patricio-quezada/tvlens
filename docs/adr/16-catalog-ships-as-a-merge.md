---
adr: 16
title: Shipping the catalog is a merge, and spoken-for shows are undeletable
status: accepted
date: 2026-09-01
tags:
  - adr
relates:
  - "[[03-identifiers]]"
---
# 16. Shipping the catalog is a merge, and spoken-for shows are undeletable

**A catalog update merges into the database the user already has, row by row, and no command
may delete a Show that any user row points at.** Nothing a user made (a rating, a review, a
watchlist entry, a tag) changes or disappears when the catalog does.

## Context

The catalog and the user's data live in the same SQLite file but belong to different people.
Shows, cast, crew and the Layer 1 graph come from TMDb and are the same for everyone. Ratings
are private and theirs. Day 27's idea was to ship the catalog like a dictionary ships with a
spell checker: a build artifact, refreshed by the developer, never touched by the user.

The metaphor breaks on a foreign key. A rating points at a specific show row, so handing over
a fresh catalog file is the new-phone-book move: every note written in the old margins is
lost. Splitting catalog and user data into two databases does not rescue it, because neither
Django nor SQLite enforces a foreign key across database files, and an unenforced link is how
ratings quietly rot.

Patricio's requirement, verbatim: "the catalog changes should be unnoticeable by the user."

## Decision

A catalog release is a merge run against the live database, using machinery that already
existed: `refresh_catalog` re-hydrates stale shows and sweeps Discover, `ingest_show` is
`update_or_create` on `tmdb_id` with the primary key pinned to it (ADR-03), so fresher data
lands in the same row every user row already points at. A fresh install ships the developer's
catalog with empty user tables; a live install runs the same commands and keeps everything.

The invariant, promoted from an implementation detail to a contract: **no command may delete
a spoken-for Show.** Spoken for means a Rating, Review, Watchlist row or ShowTag points at it.
`prune_low_signal` already excluded those shows from deletion; `CatalogShipTests` now freezes
that behavior, along with identity across re-ingest and the full fall-below-the-floor cycle.
No tombstones and no orphan policy were built, because the orphan case is unrepresentable.

Updates are deliberate. No scheduler refreshes the catalog behind anyone's back; it changes
when an operator runs the command, so the home page may move at a known moment and never
silently overnight. "Unnoticeable" means nothing of theirs moves. Recommendations are allowed
to get better.

Alternatives that lost: shipping the SQLite file wholesale (orphans every rating), a second
database for the catalog (unenforceable foreign keys), tombstone rows for departed shows
(solves a case the invariant makes impossible).

## After Action Review

The surprise was how much already worked: the protection existed, unstated and untested. The
whole build was three tests and this record. Tested in `shows/tests.py::CatalogShipTests`.
Open item: none. Revisit if user data ever moves out of the catalog's database, because the
foreign-key argument is the load-bearing one.
