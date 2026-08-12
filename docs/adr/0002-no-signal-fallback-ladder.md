# 2. Fall back down a ladder when the people graph has no signal

## Context
The weighted recommender (ADR-0001) scores by episode share. A show with **no
episodes recorded yet** (TMDb "Planned" / "In Production" legitimately carry 0), or
one whose every shared edge is a null-count series credit, produces an all-zero
score. Left alone, the merged list would silently fall back to popularity order
under a "similar shows" heading, a popularity list wearing a similarity costume.
Popularity is the exact engagement metric this recommender exists to reject.

## Decision
When the weighted score carries no signal, the **order** falls down a ladder, and
only the order; the candidate set (shows sharing at least one qualifying person)
never changes:

1. **weighted** — `sum of min(share_A, share_B)`, the normal path.
2. **estimated** — when every weighted score is 0, rank by the half we *can* see:
   the sum of the candidate-side shares (`episode_count on candidate / candidate
   episodes`). A candidate whose shared person carried their whole show outranks one
   where they were a one-episode guest. Ties break on rating then votes.
3. **rating** — when even the estimate has no signal (every edge null), rank by TMDb
   `vote_average` then `vote_count`, so a 10.0 on three votes cannot beat an 8.9 on
   ten thousand. Rating is a **quality** signal; popularity is the engagement signal,
   and it is never used here.

The page captions each mode honestly (`RankedShows.mode`), so a fallback list never
claims to be something it is not.

Alternatives rejected: hide the section (throws away real, if partial, information);
drop straight to rating (ignores the knowable candidate-side share, the original
decision, revised the same day to add the estimate rung).

## Consequences
- A brand-new show with no episodes still gets a sensible ranking from the half of
  each edge that is known, instead of a popularity list or a blank.
- Three modes, each frozen by tests in `shows/tests.py` (13 total after this).
- Extends ADR-0001.
