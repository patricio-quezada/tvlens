# TVLens

A **MovieLens-inspired** TV show recommendation platform built with Django and powered by [TMDB](https://www.themoviedb.org/).

TVLens lets users discover TV shows, rate them on a 0.5–5 star scale, write reviews, tag shows with descriptive labels, and receive personalized recommendations — all backed by real data from The Movie Database.

## Features

- **Discover & Search** — Browse trending and popular TV shows ingested from TMDB
- **Ratings** — Rate shows on a 0.5–5.0 scale (MovieLens-style)
- **Reviews** — Write and read reviews with spoiler flags
- **Tagging** — Apply community-driven tags with relevance scores (inspired by MovieLens genome tags)
- **Watchlist** — Save shows for later with priority levels
- **Watch History** — Track episode-level viewing progress
- **Recommendations** — Pluggable recommendation engine (collaborative, content-based, hybrid, popularity)
- **Rich Data Model** — 15 tables covering shows, seasons, episodes, cast, crew, and more
- **Admin Dashboard** — Full Django admin with inlines, filters, and autocomplete

## Data Model (15 Tables)

| # | Table | Description |
|---|-------|-------------|
| 1 | Genre | TV genres from TMDB |
| 2 | Network | Broadcast/streaming networks |
| 3 | Show | Core show metadata |
| 4 | Season | Season-level details |
| 5 | Episode | Episode-level details |
| 6 | Person | Actors, directors, writers |
| 7 | CastMember | Show ↔ Person (acting roles) |
| 8 | CrewMember | Show ↔ Person (crew jobs) |
| 9 | Rating | User ratings (0.5–5.0) |
| 10 | Review | User reviews with spoiler flags |
| 11 | Watchlist | User watchlist with priority |
| 12 | WatchHistory | Episode-level watch tracking |
| 13 | Tag | Community tag labels |
| 14 | ShowTag | User-applied tags with relevance |
| 15 | Recommendation | Generated show recommendations |

## Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/YOUR_USERNAME/tvlens.git
cd tvlens

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — add your TMDB_API_KEY and a SECRET_KEY

# 5. Run migrations
python manage.py migrate

# 6. Create a superuser
python manage.py createsuperuser

# 7. (Optional) Ingest shows from TMDB
python manage.py ingest_shows --pages 5

# 8. Start the development server
python manage.py runserver
```

Visit **http://127.0.0.1:8000/admin/** to explore the data.

## Management Commands

| Command | Description |
|---------|-------------|
| `python manage.py ingest_shows` | Ingest 5 pages of popular shows |
| `python manage.py ingest_shows --pages 20` | Ingest 20 pages (~400 shows) |
| `python manage.py ingest_shows --show 1396` | Ingest a single show by TMDB ID |

## Project Structure

```
tvlens/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── tvlens/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── shows/
│   ├── __init__.py
│   ├── apps.py
│   ├── models.py          # 15 tables
│   ├── admin.py            # Full admin config
│   ├── views.py
│   ├── urls.py
│   ├── tmdb_client.py      # TMDB API wrapper
│   ├── ingestion.py        # Data ingestion pipeline
│   ├── migrations/
│   └── management/
│       └── commands/
│           └── ingest_shows.py
├── templates/
│   └── shows/
│       └── index.html
└── static/
```

## TMDB API Key

TVLens uses the free [TMDB API](https://developer.themoviedb.org/docs/getting-started) for show data. Sign up at [themoviedb.org](https://www.themoviedb.org/signup) and generate an API key under **Settings → API**.

## License

MIT
