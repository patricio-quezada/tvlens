# TVLens 80-Day Project — build queue

One issue per build day, taken from the top. Order is a **queue, not a calendar**:
a missed day means the next pass picks up the same next row. Reorder freely.

Reconciled every Sunday as part of the weekly review, because a queue that lists
shipped work as `queued` is worse than no queue — it gets believed.

| Order | Issue | Status | What |
|-------|-------|--------|------|
| — | #1  | shipped Day 14 | Recommender scale hardening (ADR-06) |
| — | #5  | shipped Day 17 | Rating slice, 0.5 to 5.0 |
| — | #6  | shipped Day 18 | Layer 2 re-ranking (ADR-08) |
| — | #15 | shipped Day 18 | Top Picks, lift over the global baseline |
| — | #10 | shipped Day 19 | Side Quests (ADR-09, amended twice) |
| — | #12 | shipped Day 20 | Rating widget: fill, on-page score, save on click |
| — | #18 | shipped Day 20 | Rating saves in place (ADR-10) |
| — | #19 | shipped Day 20 | One scale behind every star |
| — | #17 | shipped Day 20 | One visual identity (ADR-11) |
| — | #11 | shipped Day 20 | My Ratings page |
| — | #9  | shipped Day 20 | Genre pages as a catalog grid |
| — | #16 | shipped Day 20 | Demo papercuts, all five items |
| 1  | #8  | queued | Search — a design day before a build day, its own scope |
| 2  | #7  | queued | Personalized context box |
| 3  | #4  | queued | Callout voice / "why recommended" |
| 4  | #3  | queued | Per-pair "See all N" shared-people view |
| 5  | #13 | queued | Actor pages *(design not recorded: propose first)* |
| 6  | #14 | in-review | Trailers / YouTube link |
| 7  | #20 | blocked | Side Quests constants — needs a catalog past 100 shows |

## How the queue works

1. Take the **topmost `queued`** row.
2. **If the design is not recorded anywhere, propose instead of building.** Write a
   scoped proposal with the numbers on the issue and stop. #10 carried no tag and
   still needed a proposal, because "cross-genre neighborhood walk" appeared exactly
   once in the repo, in a TODO, and the reading everyone assumed returned nothing for
   63 of 100 shows. A phrase that sounds decided is not a decision.
3. Otherwise build it on a branch. Never master, never push.
4. Mark the row `in-review` so the next pass moves on.

Patricio reviews, tests against the local demo, and ships. The daily post is his.

## Where the work comes from

Two sources, and the second one has been better:

- **The queue**, which is planning.
- **Using the product**, which is not. Every issue filed on 19 and 22 August came
  from opening the site and rating a show, including four that planning would never
  have produced. The weekly review has a demo pass for this reason.
