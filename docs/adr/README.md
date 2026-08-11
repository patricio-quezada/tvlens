# Architecture Decision Records

This folder holds the **why** behind TVLens's significant technical decisions, so a
future engineer reading the code can find the reasoning without leaving the repo.

- One markdown file per decision, numbered in order (`0001-...`, `0002-...`).
- Only **significant, contested, structural** decisions get an ADR (the ones with
  real alternatives and lasting impact). Not every change. Aim for a handful, not a log.
- Line-level "why" stays in code comments. Change-level "why" stays in commit messages.
  ADRs are for the decisions those smaller notes assume.
- Reference an ADR from the code it governs, e.g. `# see docs/adr/0001-...`.

Copy `0000-adr-template.md` to start a new one.

## Index
- [0001 — Recommend by episode-weighted shared people](0001-episode-weighted-people-recommender.md)
- [0002 — Fall back down a ladder when the people graph has no signal](0002-no-signal-fallback-ladder.md)
- [0003 — Three identities: surrogate pk, tmdb_id, slug](0003-identifiers.md)
- [0004 — Ingest aggregate_credits, and record episode counts](0004-aggregate-credits-ingest.md)
- [0005 — Exclude casting roles from the crew edge](0005-exclude-casting-roles.md)
