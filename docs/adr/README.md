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
