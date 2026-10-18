# REVIEW.md

What a reviewer of TVLens (a person, `/code-review`, or a subagent) checks for.
For the stack and the commands, see `CLAUDE.md`.

## What failure looks like here
- **One reader's data shows up for another.** Ratings, tags, and requests are
  private to each reader (ADR-14). Any query on Rating, ShowTag, WatchHistory,
  or ShowRequest that isn't filtered to `request.user` is High.
- **A recommendation row shows a show the reader has already seen** (#27).
- **TMDb ids mixed up with primary keys.** `Show.tmdb_id` is TMDb's id and
  `Show.id` is ours. They're different numbers.
- **The graph goes stale.** Anything that adds or removes shows has to rebuild
  the graph afterwards (ADR-07), or the new shows have no connections.
- **SQLite locking.** A long write inside a request, or two writers at once
  (the nightly job is the other writer). See the WAL notes in `settings.py`.
- **Migrations that can lose data on the live volume.** Dropping or renaming
  a column, or a data migration with no way back, is High.
- **Settings that become unsafe** when DEBUG is off, such as hosts, CSRF,
  HTTPS, or secrets.
- **Sign-up that gets around the invite codes**, or an invite code that works twice.
- **Open redirects** through `next`.

## Skip
- `demo/`, `docs/artifacts/`, `staticfiles/`, and generated migrations, unless
  the change is to the migration itself.

## Limits
- At most 5 findings. A nit counts only on changed lines.
- Cite file:line and a concrete failure scenario for every finding. Don't guess.
- Label each finding Important, Nit, or Pre-existing.
- Start with "N issues found" or "No issues found."

## Rounds
- After 3 rounds of review and fix, question the approach instead of
  reviewing again.
- After the first pass, re-review only the new diff, and never repeat a
  finding already raised.
