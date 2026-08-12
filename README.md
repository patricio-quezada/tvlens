# TVLens

**TVLens is a MovieLens-style TV show recommendation platform.** Rate the shows you
have seen, track what you are watching, and get recommendations for what to watch next.

I am building it in public to learn engineering and coding, one day at a time, on
Django and the TMDb API. The site is live at [tvlens.org](https://tvlens.org), and
[I logged the build for the first 80 days of TVLens](https://patricioquezada.com/80p).

If you want to understand the why behind certain choices for TVLens, you can
[browse the ADRs](docs/adr/).

## What is being built now

The current focus is the first layer of the recommender, the people graph:
**[Recommender: Layer 1](https://github.com/patricio-quezada/tvlens/milestone/1)**. That
milestone is the active bet, and the reasoning behind it lives in the decision
records below.

## How it works: the content graph

The recommender is the heart of TVLens, and its first layer is a content graph. Two
shows are connected when they share people, and the strength of the connection is
weighted by how much of each show the
shared person actually made. A series lead counts for a whole show; a one-episode
guest counts for a sliver. Cast and crew merge into one ranked list, and it works with
no ratings at all, so even a brand new catalog still recommends.

The significant, contested decisions each carry a short record:

- [ADR-0001, Episode-weighted people recommender](docs/adr/0001-episode-weighted-people-recommender.md)
- [ADR-0002, No-signal fallback ladder](docs/adr/0002-no-signal-fallback-ladder.md)
- [ADR-0003, Identifiers: pk, tmdb_id, slug](docs/adr/0003-identifiers.md)
- [ADR-0004, Ingest aggregate_credits](docs/adr/0004-aggregate-credits-ingest.md)
- [ADR-0005, Exclude casting roles](docs/adr/0005-exclude-casting-roles.md)

The recommender lives in [`shows/recommenders.py`](shows/recommenders.py); ingestion
in [`shows/ingestion.py`](shows/ingestion.py); the data model in
[`shows/models.py`](shows/models.py).

## Data model

Sixteen tables in `shows/models.py`. TMDb-sourced metadata covers shows, seasons,
episodes, genres, networks, and people, with separate cast and crew join tables that
carry per-episode counts. User interactions span ratings, reviews with spoiler flags,
watchlists with priority, and episode-level watch history. Community tagging uses a
shared vocabulary plus per-user applied tags with relevance scores. Generated
recommendations are stored per user per algorithm type. A `UserProfile` extends
`auth.User` one-to-one for future personalization.

Profiles are privacy-first, with no public surface, matching MovieLens. Personalization
will combine explicit half-star ratings, implicit signals (completion, drop point,
watch velocity), and community tags, with a popularity cold-start.

## Built with

Django 6.0 and SQLite, server-rendered with no JS framework. Data from the TMDb API.
Dependencies: `django-extensions`, `python-dotenv`, `requests`.

## License

MIT
