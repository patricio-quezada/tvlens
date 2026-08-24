# Data model, what a show actually has

Only design with fields that exist. These come straight from the app's `Show` model and
the recommender output.

## For the show itself
- `name` — title
- `first_air_date` → year
- `tagline` — short line (e.g. "Change the equation.")
- `overview` — the synopsis paragraph
- `status` — Ended, Returning Series, Canceled, In Production, Planned
- `vote_average` (0 to 10) and `vote_count` — the TMDb rating
- `number_of_seasons`, `number_of_episodes`
- `genres` — list (e.g. Crime, Drama)
- `networks` — list (e.g. AMC)
- `poster_url` — a real image URL (TMDb-hosted, loads in a browser)
- backdrop image is available too (a wide still)

## For each similar show (the recommendation)
- All of the show fields above, plus:
- `score` — the weighted similarity score (higher = more similar). Internal; show a cue,
  not the raw number, unless it helps.
- `shared_count` — how many people the two shows share
- `shared_people` — the list that matters, each with:
  - `name` — the person (e.g. "Vince Gilligan")
  - `contribution` — how strongly they tie the two shows (0 to 1)
  - `eps_on_source`, `eps_on_candidate` — episode counts on each show

## Not yet built (do not design around these as if they exist)
- `slug` for the URL is being added as part of this page.
- Ratings, reviews, watchlist, tags exist in the model but are not wired to this page yet.
- Person pages do not exist yet (shared-person names are not clickable destinations yet).
