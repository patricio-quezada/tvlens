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

- `walkthrough.md` the script to read while showing the app, in story order
- `screenshots/` PNGs captured as the live user, six frames telling one story
  in order (a small caption strip with tvlens.org is burned into frames 1-5;
  frame 6 is the end card, rendered by ffmpeg, not the browser):
  - `01-top-picks.png` what test rated
  - `02-watch-next.png` the answer
  - `03-detail-hero.png` opens a pick, Game of Thrones
  - `04-detail-why.png` the plain-language "why", shared people
  - `05-side-quests.png` the surprise
  - `06-end-card.png` tvlens.org in large text

## The social video

`video/tvlens-demo.mp4` is a silent, text-captioned walkthrough for posting on
social (autoplays muted, so the on-screen text carries it). 1920x1080, H.264,
30fps, under a minute, no audio track. It is real recorded browser motion in
story order (Top Picks, Watch Next, the Game of Thrones "why" reasons, Side
Quests), title card first, closing card last with tvlens.org in large text.

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
