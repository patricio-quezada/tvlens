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
| — | #8  | shipped 2026-08-24 | Catalog search, one query per branch (ADR-12) |
| — | #14 | shipped Day 21 | Trailers / YouTube link |
| — | #21 | shipped 2026-08-29 | Homepage branding: the name stated once, in the bar |
| — | #7  | shipped 2026-08-29 | Personalized context box: connection-type preference (ADR-15) |
| — | #4  | shipped 2026-08-29 | Callout voice refinement — closed inside #7's change |
| — | #20 | shipped 2026-08-27 | Side Quests' novelty gate graded, not binary (ADR-09 amendment) |
| — | #3  | swept 2026-08-25 | Per-pair shared-people view — never built; moved to Fizzy #168 |
| — | #13 | swept 2026-08-25 | Actor pages — never built; moved to Fizzy #169 |
| — | #22 | swept 2026-08-31 | Rating-above-crowd signal — shaped, not bet: the baseline is nearly flat, so lift and raw score rank identically and a badge would say nothing #19's score doesn't; reopens if that stops holding. Moved to Fizzy #174 |

#20's row-composition idea (cap one dominant genre cluster so the rest of the
row reads as surprising) was measured, not shipped: the issue's own close says
"recommending nothing," and that half of it is still open, unowned by any
Fizzy card found so far.

Nothing is `queued` here as of 2026-09-01. Recent triage (#3, #13, #22) moved
unbuilt backlog into Fizzy rather than closing it into this table, so a
reader looking for what is next should check there, not assume this queue is
simply empty.

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
