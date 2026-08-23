# TVLens

**TVLens is a MovieLens-style TV show recommendation platform.** Rate the shows you
have seen, track what you are watching, and get recommendations for what to watch next.

I am building it in public to learn engineering and coding, one day at a time, on
Django and the TMDb API. The site is live at [tvlens.org](https://tvlens.org), and
[I logged the build for the first 80 days of TVLens](https://tvlens.org/80dp/), alongside [the decisions behind it](https://tvlens.org/decisions/).

If you want to understand the why behind certain choices for TVLens, you can
[browse the ADRs](docs/adr/).

## What is being built now

The first layer of the recommender, the people graph, is built and now precomputed
([ADR-07](docs/adr/07-materialized-recommendations.md)). The active direction is
**Layer 2, personalized re-ranking** ([ADR-08](docs/adr/08-layer2-personalized-reranking.md)):
bending each user's list toward the shows they rate highly, starting with wiring the ratings
that feed it. The reasoning behind every step lives in the decision records below.

## How it works: the content graph

The recommender is the heart of TVLens, and its first layer is a content graph. Two
shows are connected when they share people, and the strength of the connection is
weighted by how much of each show the
shared person actually made. A series lead counts for a whole show; a one-episode
guest counts for a sliver. Cast and crew merge into one ranked list. The whole graph is
precomputed and served from a table, so each page load is a fast lookup rather than a live
computation ([ADR-07](docs/adr/07-materialized-recommendations.md)).

The significant, contested decisions each carry a short record:

- [ADR-01, Exclude casting roles](docs/adr/01-exclude-casting-roles.md)
- [ADR-02, Ingest aggregate_credits](docs/adr/02-aggregate-credits-ingest.md)
- [ADR-03, Identifiers: pk, tmdb_id, slug](docs/adr/03-identifiers.md)
- [ADR-04, Episode-weighted people recommender](docs/adr/04-episode-weighted-people-recommender.md)
- [ADR-05, No-signal fallback ladder](docs/adr/05-no-signal-fallback-ladder.md)
- [ADR-06, Lift the SQL variable ceiling](docs/adr/06-sql-variable-ceiling.md)
- [ADR-07, Materialize the Layer 1 ranking](docs/adr/07-materialized-recommendations.md)
- [ADR-08, Layer 2: personalized re-ranking](docs/adr/08-layer2-personalized-reranking.md)

The recommender lives in [`shows/recommenders.py`](shows/recommenders.py); ingestion
in [`shows/ingestion.py`](shows/ingestion.py); the data model in
[`shows/models.py`](shows/models.py).

## Data model

TMDb-sourced metadata covers shows, seasons, episodes, genres, networks, and people, with separate cast and crew join tables that
carry per-episode counts. User interactions span ratings, reviews with spoiler flags,
watchlists with priority, and episode-level watch history. Community tagging uses a
shared vocabulary plus per-user applied tags with relevance scores. Generated
recommendations are stored per user per algorithm type. A `UserProfile` extends
`auth.User` one-to-one for future personalization.

Profiles are privacy-first, with no public surface, matching MovieLens. Personalization
re-ranks the graph per user ([ADR-08](docs/adr/08-layer2-personalized-reranking.md)):
explicit half-star ratings and watched status feed signed genre and tag weights, with a
quality-based cold-start rather than popularity.

## Built with

Django 5.2 and SQLite, server-rendered with no JS framework. Data from the TMDb API.
Dependencies: `django-extensions`, `python-dotenv`, `requests`.

## License

MIT
