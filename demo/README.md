# TVLens local demo

Everything needed to show the current TVLens app on this machine. Local only,
nothing here deploys or publishes.

## Run it

From the repo root (`~/Work/tvlens`):

```bash
# 1. Start the dev server against the existing local database.
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

The catalog and the live user already live in `db.sqlite3`, so there is no
migrate or seed step for the demo. If you ever start from an empty database, the
catalog is rebuilt with the ingestion commands in `shows/management/commands/`
(see the main README); that is a long TMDb pull, not part of showing the app.

```bash
# 2. Open the home page.
http://127.0.0.1:8000/

# 3. Log in as the live user.
#    Username: test
#    Password: tvlens-demo
```

Log in first, then open `/` again so the home page renders as `test`. The five
recommendation rows only appear for a signed-in user with ratings.

Good pages to show:

- `/` the home page, all five rows
- `/shows/game-of-thrones/` a detail page, for the "why connected" sentences
- `/my-ratings/` what the user has told TVLens
- `/search/?q=...` catalog search

## The live user

`test` has 11 hand-picked ratings (Game of Thrones, The Wire, House, Sons of
Anarchy, Bluey, Westworld, Grey's Anatomy, Fringe, The West Wing, The 100, Silo),
all 4 stars or higher, plus a handful of tags. That spread is what drives the
personalized rows. Nothing was added or changed for this demo except setting a
known password (see below).

## What is in this folder

- `walkthrough.md` the script to read while showing the app
- `screenshots/` PNGs captured as the live user:
  - `01-home-full.png` the whole home page
  - `02-row-watch-next.png`
  - `03-row-top-picks.png`
  - `04-row-recently-added.png`
  - `05-row-side-quests.png`
  - `06-row-browse-by-genre.png`
  - `07-detail-game-of-thrones.png` a detail page with the graph explanations

## The social video

`video/tvlens-demo.mp4` is a silent, text-captioned walkthrough for posting on
social (autoplays muted, so the on-screen text carries it). 1920x1080, H.264,
30fps, ~41 seconds, no audio track. It is real recorded browser motion: a title
card, a scroll through the home rows, the Game of Thrones detail page with its
plain-language "why" reasons, and a closing card.

Rebuild it (server must be up on port 8011, wifi on so posters load):

```bash
.venv/bin/python manage.py runserver 127.0.0.1:8011 &
.venv/bin/python demo/video/record_walkthrough.py   # records raw/walkthrough-raw.webm
bash demo/video/build_video.sh                       # composes tvlens-demo.mp4
```

`demo/video/txt/` holds the exact caption text, so edits are one file each.

## Reproducing the screenshots

The server must be running (on port 8007 for the capture script, or edit the
`BASE` line). Selenium was installed into the venv and drives the system
Chromium via `/usr/bin/chromedriver`:

```bash
.venv/bin/pip install selenium        # already done in this venv
.venv/bin/python manage.py runserver 127.0.0.1:8007 &
.venv/bin/python demo/shoot.py        # writes into demo/screenshots/
```

## One change to note

The `test` user's password was set to `tvlens-demo` so you can log in tonight.
That is the only mutation this demo made to the database; no code, no catalog, no
ratings were touched. Change it whenever you like:

```bash
.venv/bin/python manage.py changepassword test
```
