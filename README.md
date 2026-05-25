# TVLens

MovieLens-style TV show recommendation platform. Django + TMDb API.

Privacy-first profiles (no public surface, matching MovieLens). Personalization stack: explicit ratings (0.5–5.0, half-star) + implicit signals (completion, drop point, watch velocity) + community tags + content-based fallbacks. Cold-start via popularity until rating volume is sufficient.

## Data model

16 tables in `shows/models.py`:

| # | Table | Description |
|---|-------|-------------|
| 1 | Genre | TMDb genres |
| 2 | Network | Broadcast/streaming networks |
| 3 | Show | Series metadata |
| 4 | Season | Per-season detail |
| 5 | Episode | Per-episode detail |
| 6 | Person | Cast and crew |
| 7 | CastMember | Show ↔ Person (acting) |
| 8 | CrewMember | Show ↔ Person (crew) |
| 9 | Rating | User ratings 0.5–5.0 |
| 10 | Review | User reviews + spoiler flag |
| 11 | Watchlist | User watchlist + priority |
| 12 | WatchHistory | Episode-level viewing |
| 13 | Tag | Community tag vocabulary |
| 14 | ShowTag | User-applied tags + relevance |
| 15 | Recommendation | Generated picks (collab / content / hybrid / popular) |
| 16 | UserProfile | OneToOne extension on `auth.User` |

## Run

Requires Python 3.12+, a TMDb v3 API key, Django 6.0+.

```bash
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # fill SECRET_KEY and TMDB_API_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py ingest_shows --pages 5
python manage.py runserver
```

App: `/`. Admin: `/admin/`. Auth: `/accounts/{login,register,password_reset}/`.

## Management commands

| Command | Effect |
|---------|--------|
| `ingest_shows` | Pull discover pages from TMDb (default 5) |
| `ingest_shows --pages N` | N pages × 20 shows |
| `ingest_shows --show <tmdb_id>` | Single show |
| `ingest_shows --sort-by <field>` | Override discover sort |

Pipeline is idempotent; reruns update via `update_or_create`.

## Layout

```
tvlens/
├── manage.py
├── requirements.txt
├── shows/
│   ├── models.py              # 16 tables
│   ├── admin.py
│   ├── forms.py               # RegistrationForm (UserCreationForm + email)
│   ├── views.py               # index, register
│   ├── urls.py
│   ├── tmdb_client.py         # TMDb v3 client (api_key query param)
│   ├── ingestion.py           # Ingestor: discover → shows → seasons → episodes → credits
│   ├── migrations/
│   └── management/commands/
│       └── ingest_shows.py
├── templates/
│   ├── base.html              # Cloud Dancer warm-dark theme
│   ├── shows/
│   │   ├── index.html
│   │   ├── _row.html          # Horizontal card carousel
│   │   └── _row_empty.html    # Cold-start empty-state row
│   └── registration/          # Django auth + custom register
└── tvlens/                    # Project config (settings, urls, wsgi)
```

## Stack

Django 6.0, SQLite (dev) / PostgreSQL (planned), `django-extensions`, `python-dotenv`, `requests`. No JS framework — server-rendered with progressive enhancement planned via HTMX.

## License

MIT
