# TVLens

**TVLens is a MovieLens-style TV show recommendation platform.** Rate the shows you
have seen, track what you are watching, and get recommendations for what to watch next.

I am building it in public to learn engineering and coding, one day at a time, on
Django and the TMDb API. [tvlens.org](https://tvlens.org) is the public build log and
decision record, a static site; [I am logging the build day by day](https://tvlens.org/80dp/),
alongside [the decisions behind it](https://tvlens.org/decisions/). The Django app in
this repository is not deployed anywhere yet. It runs locally, against the local demo.

If you want to understand the why behind certain choices for TVLens, you can
[browse the ADRs](docs/adr/).

## What is built so far

The first layer of the recommender, the people graph, is built and precomputed
([ADR-07](docs/adr/07-materialized-recommendations.md)). On top of it sits
**Layer 2, personalized re-ranking** ([ADR-08](docs/adr/08-layer2-personalized-reranking.md)):
each user's ratings bend their lists toward the shows they rate highly, and drive the
home page rows (Watch Next, Top Picks, Side Quests). The reasoning behind every step
lives in the decision records below.

## How it works: the content graph

The recommender is the heart of TVLens, and its first layer is a content graph. Two
shows are connected when they share people, and the strength of the connection is
weighted by how much of each show the shared person actually made. A series lead
counts for a whole show; a one-episode
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
- [ADR-07, Precompute the Layer 1 ranking](docs/adr/07-materialized-recommendations.md)
- [ADR-08, Layer 2: personalized re-ranking](docs/adr/08-layer2-personalized-reranking.md)

Later decisions (Side Quests, search, tags, how the catalog ships) live in
[the full index](docs/adr/README.md).

The recommender lives in [`shows/recommenders.py`](shows/recommenders.py); ingestion
in [`shows/ingestion.py`](shows/ingestion.py); catalog search in
[`shows/search.py`](shows/search.py); the data model in
[`shows/models.py`](shows/models.py).

## Search

Search covers titles, cast, crew, characters, descriptions, genres, networks, tags,
season names and episode synopses. A year or season number typed into the box filters
with no operator; `actor:`, `genre:`, `tag:`, `score:>8` and the rest scope to one field
and stack by intersection. A misspelling falls back to the closest real name in the
catalog. Episode synopses are served from an FTS5 index and ranked by bm25; every other
branch runs as its own query, matched on word boundaries. Why it is built that way
rather than as one query: [ADR-12](docs/adr/12-catalog-search.md).

## Data model

TMDb-sourced metadata covers shows, seasons, episodes, genres, networks, and people, with separate cast and crew join tables that
carry per-episode counts. User interactions span ratings, reviews with spoiler flags,
watchlists with priority, and episode-level watch history. Community tagging uses a
shared vocabulary plus per-user applied tags with relevance scores: the `Tag` row is
shared so two readers can agree what "slow burn" means, while the `ShowTag` row belongs
to one person. A `UserProfile` extends `auth.User` one-to-one for future personalization.

Layer 1 recommendations are materialized into `SimilarShow`
([ADR-07](docs/adr/07-materialized-recommendations.md)) rather than stored per user. A
`Recommendation` model was removed in favor of that table, since nothing wrote to it.
A catalog update merges into the live database row by row, and no command may delete a
show that any user's data points at ([ADR-16](docs/adr/16-catalog-ships-as-a-merge.md)).

Profiles are privacy-first, with no public surface, matching MovieLens. Personalization
re-ranks the graph per user ([ADR-08](docs/adr/08-layer2-personalized-reranking.md)):
explicit half-star ratings and watched status feed signed genre and tag weights, with a
quality-based cold-start rather than popularity.

## Built with

Django 6.1 and SQLite, server-rendered with no JS framework. Data from the TMDb API.
Runtime dependencies: `python-dotenv` and `requests`. Development adds
`django-extensions` and `ruff`.

## License

MIT
