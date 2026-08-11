# TVLens

A TV show recommendation platform that connects shows through **the people who made
them**, cast and crew, not through what keeps you watching. Think MovieLens, for
television. Built in public, one day at a time.

Django + the TMDb API.

## Start here

New to the project? These four links get you oriented.

- **[The 80-Day Project build log](https://patricioquezada.com/80p)** — the daily
  story of building this: what got built each day and what I learned. Start here to
  follow the narrative from Day 0.
- **[Architecture Decision Records](docs/adr/)** — *why* the project is built the way
  it is. The significant, contested decisions, each with its reasoning. Start here if
  you are reading the code and wondering "why this way?"
- **[Open issues](https://github.com/patricio-quezada/tvlens/issues)** and the
  **[current milestone](https://github.com/patricio-quezada/tvlens/milestone/1)** —
  what is being worked on right now.
- **[tvlens.org](https://tvlens.org)** — the landing page for the product.

## How the recommender works

The heart of TVLens is a content graph. Two shows are connected when they share
people, and the strength of the connection is weighted by how much of each show the
shared person actually made. A series lead counts for a whole show; a one-episode
guest counts for a sliver. Cast and crew merge into one ranked list. The full
reasoning is in the ADRs:

- [ADR-0001 — Episode-weighted people recommender](docs/adr/0001-episode-weighted-people-recommender.md)
- [ADR-0002 — No-signal fallback ladder](docs/adr/0002-no-signal-fallback-ladder.md)
- [ADR-0003 — Identifiers: pk, tmdb_id, slug](docs/adr/0003-identifiers.md)
- [ADR-0004 — Ingest aggregate_credits](docs/adr/0004-aggregate-credits-ingest.md)
- [ADR-0005 — Exclude casting roles](docs/adr/0005-exclude-casting-roles.md)

The recommender lives in [`shows/recommenders.py`](shows/recommenders.py); ingestion
in [`shows/ingestion.py`](shows/ingestion.py); the data model in
[`shows/models.py`](shows/models.py).

## Data model

Sixteen tables in `shows/models.py`. TMDb-sourced metadata covers shows, seasons,
episodes, genres, networks, and people (with separate cast and crew join tables that
carry per-episode counts). User interactions span ratings, reviews with spoiler flags,
watchlists with priority, and episode-level watch history. Community tagging uses a
shared vocabulary plus per-user applied tags with relevance scores. Generated
recommendations are stored per user per algorithm type. A `UserProfile` extends
`auth.User` one-to-one for future personalization.

Profiles are privacy-first (no public surface, matching MovieLens). Personalization
will combine explicit half-star ratings, implicit signals (completion, drop point,
watch velocity), and community tags, with a popularity cold-start.

## Stack

Django 6.0, SQLite, `django-extensions`, `python-dotenv`, `requests`. Server-rendered,
no JS framework.

## License

MIT
