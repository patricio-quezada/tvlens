# Architecture Decision Records

This folder holds the **why** behind TVLens's significant technical decisions, so a
future engineer reading the code can find the reasoning without leaving the repo.

- One markdown file per decision, numbered in the **order the decisions were made**
  (`01-...`, `02-...`), so the list reads as the project evolving over time.
- Only **significant, contested, structural** decisions get an ADR (the ones with real
  alternatives and lasting impact). Not every change. Aim for a handful, not a log.
- Each record has three parts: **Context**, **Decision**, and an **After Action Review**
  (did it work, and what it does for the product).
- Line-level "why" stays in code comments. Change-level "why" stays in commit messages.
  ADRs are for the decisions those smaller notes assume.
- Reference an ADR from the code it governs, e.g. `# see docs/adr/04-episode-weighted-people-recommender.md`.

Copy `00-adr-template.md` to start a new one.

## Index
- [01 - Exclude casting roles from crew matching](01-exclude-casting-roles.md)
- [02 - Ingest aggregate_credits, and record episode counts](02-aggregate-credits-ingest.md)
- [03 - Three identities: surrogate pk, tmdb_id, slug](03-identifiers.md)
- [04 - Recommend by episode-weighted shared people](04-episode-weighted-people-recommender.md)
- [05 - Fall back down a ladder when the people graph has no signal](05-no-signal-fallback-ladder.md)
- [06 - Lift the SQL variable ceiling in the recommenders](06-sql-variable-ceiling.md)
- [07 - Materialize the Layer 1 ranking and serve reads from a table](07-materialized-recommendations.md)
