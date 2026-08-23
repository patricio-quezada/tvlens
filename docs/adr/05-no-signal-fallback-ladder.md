# 5. Fall back down a ladder when the people graph has no signal

## Context
The weighted recommender ([ADR-04](04-episode-weighted-people-recommender.md)) ranks
shows by how much of each one a shared person actually made. But some shows have no
episode counts to work with: a brand new or unaired show legitimately has zero episodes,
and some shows carry only series-level credits with no per-episode numbers. For those,
every score comes out zero.

If I sorted by an all-zero score, the list would quietly fall back to the database's
default order, which is roughly most-popular-first, under a heading that says "similar
shows." That is a popularity list pretending to be a similarity list, and popularity is
exactly what this recommender is built to avoid.

## Decision
When the score has nothing to work with, only the order changes, never the set of
candidates (still every show that shares at least one qualifying person). The order
falls back in three steps:

1. **Weighted, the normal path.** Rank by the shared-people score from
   [ADR-04](04-episode-weighted-people-recommender.md).
2. **Estimated.** When every weighted score is zero, rank by the half I can still see:
   how much of the candidate show the shared person made. A candidate whose shared
   person led their whole show ranks above one where that person was a one-episode
   guest. Ties break on rating, then number of votes.
3. **Rating.** When even that has nothing to go on, rank by the TMDb rating, with the
   number of votes as a tiebreaker so a 10 from three votes cannot beat an 8.9 from ten
   thousand. Rating measures quality, not popularity, so it stays true to the point of
   the recommender.

Each list says which step it used, so a fallback ranking never pretends to be a full
match.

I considered hiding the section when there is no score, but that throws away the partial
information I do have. I also considered dropping straight to the rating, but that skips
the candidate-side estimate I can still measure, so I kept the middle step.

## After Action Review
For someone using TVLens, a brand new or barely-recorded show still shows a sensible list
of similar shows instead of a blank space or a popularity chart in disguise. The list is
honest about how sure it is: a full match, an estimate, or just a quality rating. Tests
lock each of the three modes in, so a later change cannot quietly turn the estimate back
into popularity.

Extends [ADR-04](04-episode-weighted-people-recommender.md).
