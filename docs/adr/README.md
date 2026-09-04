# Architecture Decision Records

This folder holds the **why** behind TVLens's significant technical decisions, so a
future engineer reading the code can find the reasoning without leaving the repo.

- One markdown file per decision, numbered in the **order the decisions were made**
  (`01-...`, `02-...`), so the list reads as the project evolving over time.
- Only **significant, contested, structural** decisions get an ADR (the ones with real
  alternatives and lasting impact). Not every change. Aim for a handful, not a log.
- Each record opens with a **bottom line up front**: one or two sentences giving the decision
  and its reason before anything else, so a reader who followed a reference from the code can
  stop there.
- Then three parts: **Context**, **Decision**, and an **After Action Review** (did it work, and
  what it does for the product).
- Write them to be read. The BLUF serves the reader in a hurry, which frees the body to tell the
  story properly: open on the moment the problem showed up, keep paragraphs short, and prefer a
  concrete picture to a precise abstraction.
- Line-level "why" stays in code comments. Change-level "why" stays in commit messages.
  ADRs are for the decisions those smaller notes assume.
- Reference an ADR from the code it governs, e.g. `# see docs/adr/04-episode-weighted-people-recommender.md`.

Copy `00-adr-template.md` to start a new one.

## Reading this from another project

The index below is chronological, because these records are also the story of
how TVLens changed its mind. If you have arrived from another codebase and want
the transferable part, read in this order instead.

**Patterns that are not about television at all.** These would be the same
decision in any app that ingests a catalog from someone else's API and serves
rankings off it.

- [03](03-identifiers.md) and [13](13-child-record-identity.md): **identity.**
  Three identities per record, and what to do when the upstream API keeps none
  of them stable. Read both; 13 is the correction 03 needed.
- [07](07-materialized-recommendations.md): **precompute a pure function.** If
  the answer depends only on the catalog, it is a table, not a request.
- [05](05-no-signal-fallback-ladder.md): **degrade down a ladder and say which
  rung you are on.** A weak answer offered as a strong one is the failure.
- [06](06-sql-variable-ceiling.md): **the SQLite variable ceiling**, which you
  will hit the first time a query takes a real list of ids.
- [12](12-catalog-search.md): **one query per branch**, matched on word
  boundaries, rather than one query that tries to be clever.
- [14](14-tags-shared-vocabulary.md): **a shared vocabulary applied
  privately.** How to let users label things without letting them label things
  for each other.
- [16](16-catalog-ships-as-a-merge.md): **ship catalog updates as a merge.**
  Nothing a user made changes or disappears when the catalog does, and a show
  their data points at can never be deleted.

**Shapes that transfer even though the subject does not.** The domain is TV
credits; the reasoning is about any graph built from shared entities.

- [04](04-episode-weighted-people-recommender.md): weight an edge by **how
  much** two things share, not how many things they share.
- [01](01-exclude-casting-roles.md): not every connection is a connection.
  Some roles link everything to everything and must be excluded by name.
- [08](08-layer2-personalized-reranking.md): **personalize by re-ranking, never
  by re-scoring.** Layer 1 stays the same for everyone; Layer 2 reorders it.
- [09](09-side-quests-cross-genre-edges.md): surprise needs an expectation to
  violate, so a "discovery" row cannot exist for someone who has told you
  nothing. Two reversals are recorded in it, both worth the read.
- [15](15-connection-type-preference.md): letting the user's own ratings say
  which signal they respond to, and **how to know when a difference is real**.
  Its amendment is a worked example of a statistic that looked calibrated and
  was not.

**TVLens-only.** Skip unless you are working on this app.
[02](02-aggregate-credits-ingest.md) on what TMDb returns,
[10](10-rating-saves-in-place.md) on the rating widget,
[11](11-one-visual-identity.md) on the visual identity.

## Index
- [01 - Exclude casting roles from crew matching](01-exclude-casting-roles.md)
- [02 - Ingest per-episode credits, and record episode counts](02-aggregate-credits-ingest.md)
- [03 - Three identities: pk, tmdb_id, slug](03-identifiers.md) *(amended 2026-08-30: Show's pk is its tmdb_id)*
- [04 - Recommend by episode-weighted shared people](04-episode-weighted-people-recommender.md)
- [05 - Fall back down a ladder when the people graph has no signal](05-no-signal-fallback-ladder.md)
- [06 - Lift the SQL variable ceiling in the recommenders](06-sql-variable-ceiling.md)
- [07 - Precompute the Layer 1 ranking and serve it from a table](07-materialized-recommendations.md)
- [08 - Layer 2: personalize by re-ranking the graph per user](08-layer2-personalized-reranking.md)
- [09 - Side Quests: strong connections into genres a user has not rated highly](09-side-quests-cross-genre-edges.md) *(revised twice, then amended 2026-08-27: the genre gate is graded rather than binary)*
- [10 - Rating saves in place, and TVLens takes its first script](10-rating-saves-in-place.md)
- [11 - One visual identity, defined once, taken from the landing page itself](11-one-visual-identity.md)
- [12 - Catalog search: one query per branch, matched on word boundaries](12-catalog-search.md) *(amended 2026-09-01: the episode branch reads FTS5)*
- [13 - Resolve seasons and episodes by two keys, because TMDb keeps neither stable](13-child-record-identity.md) *(amends ADR-03 for Season and Episode)*
- [14 - Tags are a shared vocabulary applied privately](14-tags-shared-vocabulary.md)
- [15 - Learn whether a reader is tied to shows by cast or by crew, and name accordingly](15-connection-type-preference.md) *(amended 2026-08-30: the gate is a permutation test, not a threshold)*
- [16 - Shipping the catalog is a merge, and spoken-for shows are undeletable](16-catalog-ships-as-a-merge.md)
