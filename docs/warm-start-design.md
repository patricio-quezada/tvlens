# Daily warm-start: design

Pre-stages the next queued issue so the morning has no cold start. It does **not**
draft code and does **not** run Claude. Originally a PowerShell script driven by
Windows Task Scheduler; that implementation was deleted on the move to Omarchy.
This is the platform-independent design, kept so it can be rebuilt natively.

## What it does

1. Read `docs/day-plan.md` and find the first row matching `| <day> | #<issue> | queued`.
2. If a worktree for that issue already exists, **do nothing and exit.** This is
   deliberate: the job must never run ahead. The next issue is only staged once the
   current one ships and its plan row stops saying `queued`.
3. Resolve the issue title with `gh issue view <n> --json title --jq .title`, slugify
   it, truncate to 30 chars.
4. `git worktree add -b prep/<slug> <path> master` — off master, never pushed.
5. Write `scripts/next-prep.md`: the worktree path, the branch, and the full issue
   text with comments from `gh issue view <n> --comments`.
6. Desktop notification that the issue is staged.

Branches are named for **what the work is** (`prep/<slug>`), never the issue number.
"Day N" always means the 80-Day-Project calendar day, never the issue number.

## Omarchy equivalents

| Windows piece | Omarchy |
|---|---|
| Task Scheduler, 0900 America/New_York | systemd user timer (`OnCalendar=*-*-* 09:00:00`) |
| PowerShell | bash |
| `git worktree add` | same, or `try worktree` (`~/Work/tries`, date-prefixed) |
| `gh issue view` | same, already authenticated |
| `System.Windows.Forms.NotifyIcon` balloon | `notify-send`, or `omarchy reminder` |
| `C:\Users\Patricio Quezada\dev\tvlens-prep-<n>` | `~/Work/tvlens-prep-<n>` or a `try` directory |

## The gotcha a rebuild must handle

A git worktree contains **only tracked files**. TVLens needs three gitignored things
to actually run, so a bare worktree is a blank app:

- `.env` — `SECRET_KEY`, `TMDB_API_KEY`
- `db.sqlite3` — ~30MB; 100 shows, 82,763 people, 128,541 cast members
- `.venv/` — or skip rebuilding it and run the worktree's code with the main
  interpreter: `~/Work/tvlens/.venv/bin/python manage.py runserver`

The database is the one that matters. Without it the staged worktree looks broken.
The original script did not handle this, because on Windows the worktree was only
ever used for drafting, never for running the app.
