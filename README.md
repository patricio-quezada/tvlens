# TVLens

MovieLens-style TV show recommendation platform. Django + TMDb API.

Privacy-first profiles (no public surface, matching MovieLens). The personalization stack combines explicit ratings on a 0.5–5.0 half-star scale, implicit signals (completion percentage, drop point, watch velocity), and community tags with relevance scores. Cold-start falls back to popularity until rating volume is sufficient.

## Data model

Sixteen tables in `shows/models.py`. TMDb-sourced metadata covers shows, seasons, episodes, genres, networks, and people (with separate cast and crew join tables). User interactions are tracked across ratings, reviews with spoiler flags, watchlists with priority, and episode-level watch history. Community tagging uses a shared vocabulary plus per-user applied tags with relevance scores. Generated recommendations are stored per user per algorithm type (collaborative, content-based, hybrid, popularity). A `UserProfile` model extends `auth.User` as a OneToOne for future personalization fields.

## Stack

Django 6.0, SQLite, `django-extensions`, `python-dotenv`, `requests`. Server-rendered. No JS framework.

## License

MIT
