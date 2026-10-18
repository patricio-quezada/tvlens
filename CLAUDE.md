# TVLens

A Django 6.1 TV recommender built on SQLite and TMDb data. It runs on Fly.io as
the app `tvlens` at app.tvlens.org. Decisions are in `docs/adr/`, and what a
review checks for is in `REVIEW.md`.

## Checks (run all of them before every deploy; CI runs them on every push)
```
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

## Ship
- **Low risk** (docs, copy, CSS, tests): run the checks, then push and deploy.
- **Medium risk** (views, search, recommenders, models with additive
  migrations): run the checks, plus a review of the diff in a fresh context
  (a subagent, or `/code-review`) against `REVIEW.md`. Fix what it finds,
  then deploy.
- **High risk** (auth, invites, email, settings, secrets, migrations that drop
  or rename data, `.github/workflows/`, `deploy/`, `fly.toml`): everything
  Medium needs, and Patricio says yes before the deploy.
- Deploy: `mise x flyctl -- fly deploy --remote-only`. Never use Fly's GitHub
  launcher.
- After a deploy, check the changed page on app.tvlens.org itself.
- Roll back by redeploying the previous image: `fly releases --image -a tvlens`,
  then `fly deploy --image <ref> -a tvlens`. A rollback does not undo a
  migration, which is another reason migrations stay additive.

## When a review misses something
Ask what context was missing, and put it in `REVIEW.md` or a test so the same
class of bug is caught next time.
