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
| 3  | #10 | done — shipped Day 19 | Side Quests: surprise against demonstrated taste |
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

## How the queue works

1. Take the **topmost `queued`** row.
2. **If the design is not recorded anywhere, propose instead of building.** Write a
   scoped proposal with the numbers on the issue and stop. This is the rule, not the
   *design-heavy* tag below: #10 carried no tag and still needed a proposal, because
   "cross-genre neighborhood walk" appeared exactly once in the repo, in a TODO, and
   the reading everyone assumed returned nothing for 63 of 100 shows. A phrase that
   sounds decided is not a decision. Check what it produces before building it.
3. Otherwise build it on a branch. Never master, never push.
4. Mark the row `in-review` so the next pass moves on.

There is no scheduled job. The 0900 Windows warm-start that used to pre-stage a
worktree was retired on 2026-08-21; the reading it front-loaded now happens on
demand. Patricio reviews, tests against the local demo, and ships (merge and push)
himself. Shipping and the daily build-in-public post stay his.

## Design-heavy issues (propose, never guess)
#9, #13, #16 carry layout/taste calls, so they get a proposal rather than a build.
This list is a prediction, not the trigger. Rule 2 above is the trigger.
