# TVLens 80-Day Project — build queue

One issue per build day. The daily prep job takes the next `queued` row, drafts it
on an **isolated branch** (never master, never pushed), posts a brief as a comment on
that GitHub issue, and leaves it for Patricio to review, test, and ship.

Order is a **queue, not a calendar.** A missed day just means the next run picks up the
same next `queued` row. Reorder rows freely — the job always works the topmost `queued`.

| Order | Issue | Status | What |
|-------|-------|--------|------|
| 1  | #6  | done — shipped Day 18 | Layer 2 re-ranking + genre-filter page |
| 2  | #15 | done — shipped Day 18 | Top Picks (lift over the global baseline) + one-show-one-row dedupe |
| 3  | #10 | queued | Side Quests on by default |
| 4  | #7  | queued | Personalized context box |
| 5  | #4  | queued | Callout voice / "why recommended" |
| 6  | #9  | queued | Genre page → catalog grid + home reset  *(design-heavy: propose)* |
| 7  | #12 | queued | Rating widget fill + on-page MovieLens display |
| 8  | #11 | queued | "My Ratings" page |
| 9  | #13 | queued | Actor pages (two-section)  *(design-heavy: propose)* |
| 10 | #3  | queued | Per-pair "See all N" shared-people view |
| 11 | #16 | queued | Polish pass (small-items checklist)  *(design-heavy: propose)* |
| 12 | #14 | queued | Trailers / YouTube link |
| 13 | #8  | queued | Search — multi-day, its own scope |

## How the prep job works each morning

1. Read this file, take the **topmost `queued`** row.
2. If the row is marked *design-heavy*, produce a **scoped proposal + questions** on the
   issue — do NOT guess at taste and build it.
3. Otherwise draft the implementation on a branch named `prep/<slug of the issue title>`
   (describe the work, not the number) in an isolated worktree. Never touch master. Never push.
4. Post a brief as a comment on the issue: what it built, how to run it locally, and any
   decisions it needs.
5. Mark the row `in-review` here so the next run moves on.

Patricio reviews, tests against the local demo, and ships (merge + push) himself. Shipping
and the daily build-in-public post stay his.

## Design-heavy issues (propose, never guess)
#9, #13, #15, #16 carry layout/taste calls. On these the job writes a proposal, not a build.
