# 5. Fall back down a ladder when the people graph has no signal

**When every weighted score comes out zero, sorting by it would quietly hand the ranking to the
database's default order, which is popularity. The list falls back through two weaker rankings
instead, and says on the page which one it used.**

## Context
Some shows score zero against everything. A brand new or unaired show legitimately has no
episodes, and some shows carry only series-level credits with no per-episode numbers, so the
weighting in [ADR-04](04-episode-weighted-people-recommender.md) has nothing to weigh.

Sort a list of identical zeros and the database returns them in its own default order. That
order is roughly most-popular-first.

Which means a heading reading "more shows like this" would sit on top of a popularity chart,
silently, exactly where the data is thinnest. Popularity is the one thing this recommender
exists to avoid, and this is how it would have crept back in.

## Decision
When the score has nothing to work with, only the *order* changes. The set of candidates never
does: it stays every show sharing at least one qualifying person. The order falls back in three
steps.

1. **Weighted, the normal path.** Rank by the shared-people score from
[ADR-04](04-episode-weighted-people-recommender.md). 2. **Estimated.** When every weighted
score is zero, rank by the half still visible: how much of the *candidate* show the shared
person made. A candidate whose shared person led their whole show ranks above one where that
person was a one-episode guest. Ties break on rating, then vote count. 3. **Rating.** When even
that has nothing to go on, rank by TMDb rating, with vote count as the tiebreaker so a 10 from
three votes cannot beat an 8.9 from ten thousand. Rating measures quality, not popularity,
which keeps it inside the rule rather than around it.

Each list says which step produced it, so a fallback ranking never passes itself off as a full
match.

I considered hiding the section entirely when there is no score, which throws away the partial
information there is. I also considered dropping straight to the rating, which skips the
candidate-side estimate that is still measurable. The middle step earns its place.

## After Action Review
A brand new or barely-recorded show now shows a sensible list instead of a blank space or a
popularity chart in disguise, and the list is honest about how sure it is: a full match, an
estimate, or a quality rating.

Tests lock each of the three modes in, so a later change cannot quietly turn the estimate back
into popularity.

Extends [ADR-04](04-episode-weighted-people-recommender.md).
