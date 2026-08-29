---
adr: 15
title: "Learn whether a reader is tied to shows by cast or by crew, and name accordingly"
status: accepted
date: 2026-08-26
tags:
  - adr
relates:
  - "[[04-episode-weighted-people-recommender]]"
  - "[[08-layer2-personalized-reranking]]"
amends:
  - "issue #2, the fixed \"pitch by cast\" clause order"
---
# 15. Learn whether a reader is tied to shows by cast or by crew, and name accordingly

**Layer 2 gains a third dimension, alongside genre and tag: the average rating signal that cast
connections and crew connections have each earned from one reader, measured on the Layer 1 edges
that fall inside that reader's own rated set. It orders the people a callout names, and only
that. It never scores a show, and it stays silent unless three gates say the ratings have
actually earned it.**

> Accepted 2026-08-29. Shipped in #7 before approval; the record caught up after.

## Context
Layer 1 scores every shared person the same way whatever they did on the show
([ADR-04](04-episode-weighted-people-recommender.md)). A creator and a lead actor are both
worth their episode share, and that is right for ranking: the graph should not decide in advance
that a showrunner matters more than a star.

Reading is a different act from ranking. "They share three of the same actors" and "the same
showrunner made both" are two different pitches, and readers do not weigh them the same way.
Patricio put it plainly on 2026-08-26:

> if I rate 10 shows and some of them that I have watched are top recommendations of others, if
> the connection is more "cast overlap heavy" instead of "crew overlap heavy" then maybe I
> prefer one over the other

That is a question the existing profile could not answer. Layer 2 knew a reader's genres and
tags; it knew nothing about the shape of the connections between the shows they liked. Issue #7
had sat in the backlog since 2026-08-19 describing itself as "the display side of Layer 2's
learned weights", which was not buildable, because no per-connection-type signal existed to
display. The shaping turned it into one piece of work: compute the signal first, then order by
it.

The hard part was never the arithmetic. It was that the database has two users, one with ten
ratings and one with none, and a recommender that asserts a taste it cannot support is worse
than one that says nothing.

## Decision
### Where the evidence comes from
Only the Layer 1 edges whose **two ends the reader has both rated**. That is the one place where
a connection and a verdict on it are visible at the same time: the reader saw show A, saw show B,
said what they thought of each, and the graph already knows what ties them together. Every other
edge in the catalog is a connection with no verdict attached.

This follows the shaping exactly, and it has a consequence worth stating out loud. The gate is
not "rate more shows", it is "rate shows that are actually connected". Measured on the catalog:
ten shows picked at random have **zero** edges between them, while the real ten-rating user,
whose ratings cluster by taste, has **twelve**. A reader who rates broadly and shallowly will
never earn a lean, and should not.

### How the two numbers are computed
For each such edge, the existing `shared_connections` gives the same episode-share contributions
that ranked the show. Each contribution is filed under **cast** or **crew** (marquee crew and
ordinary crew collapse together, because the split a reader feels is on-screen versus behind it).
The edge carries the mean of its two ends' rating signals, on the same `score - 3.0` scale the
rest of Layer 2 uses.

Each type's affinity is then the contribution-weighted mean of those signals: *the average rating
signal a cast connection earned from you*. This is the identical shape `ShowTag` relevance already
uses in `build_profile`, which is the point. Two shows tied by one whole-run shared lead weigh
more than two tied by a guest. A reader who rates everything the same gets two equal affinities
and therefore no lean, which is the correct answer rather than a failure of the method.

The lean is the signed difference, cast-positive, in stars.

### The three gates
The lean is 0.0 unless all three pass:

- **At least four directed edges** inside the rated set. Fewer and one show pair is the whole
  opinion.
- **At least 1.0 of episode-share mass on each side.** One person on the whole run of both
  shows contributes 1.0, so this asks for about one whole-run collaborator's worth of evidence
  per type. A reader whose edges are entirely cast has no crew number to compare against, and
  inventing one from a single guest director is how a recommender starts making things up.
- **At least half a star between the two affinities.** Half a star is the smallest gap that can
  be said out loud without embarrassment: "the shows you rate highly are held together by shared
  actors rather than shared crew."

The real ten-rating user in the database today **fails the third gate**. Their twelve inner edges
give cast 1.690 and crew 1.645, a gap of 0.044, because they rated every one of those shows
between 4.0 and 5.0. There is nothing to learn from a reader who liked everything, and the
feature says so by doing nothing. That is the design working, not the demo being thin.

### What the lean does
`name_connections` multiplies each shared person's contribution by their type's multiplier
before it cuts and orders the named few. A full one-star lean is a 3:1 tilt: 1.5 for the
preferred type, 0.5 for the other. Enough to reorder the middle of a callout and push a marginal
credit out of the named five; not enough to unseat a dominant shared lead. Same instinct as the
re-ranking in [ADR-08](08-layer2-personalized-reranking.md), where a blowout Layer 1 edge
resists personalization while the near-tied tail moves freely.

Measured across all 4,014 stored edges, a full lean changes **who** is named on 5.8% of edges
(cast-leaning) and 7.4% (crew-leaning), and changes the **order** of the named on 20.1%. The
"and N others" count never changes: the tail collapse is issue #2's decision and this does not
touch it.

### This amends issue #2's "pitch by cast"
`compose_callout` used to open the sentence on cast whenever any cast was named, whatever order
`name_connections` had produced. So the first cut of this work could change *who* was named but
never *what the sentence opened on*, and a crew-leaning reader still heard about an actor first.

Patricio decided on 2026-08-26 to let the lean reorder the prose blocks, amending that decision.
The amendment is recorded as a comment on issue #2 so the closed record does not contradict the
code.

The important half is the default. "Fall back to cast-first when the lean is absent or weak" is
not an error path here, because there is nothing to detect: cold start, insufficient signal,
anonymous, and a sub-half-star gap all already arrive as a lean of exactly 0.0, and `lean < 0` is
false for all of them. Those readers take the same branch they always took. That is the same
discipline the multipliers use, where a 0.0 lean makes both of them 1.0, and it is why the
fallback cannot drift away from the default over time: it *is* the default.

With a full crew lean, 1,496 of 4,014 edges (37.3%) open on crew. With no lean, none do.

### What it deliberately does not do
- **It never ranks a show.** It is not added to `score_for` and it is not a third term in the
  re-rank. Layer 2 owns genre and tag preference for ordering candidates; this dimension orders
  names inside a callout. Keeping them apart is what keeps the same signal from being counted
  twice.
- **It reads rated shows only, never watched-but-unrated ones.** `WATCHED_SIGNAL` is a constant,
  so including views would pull both affinities toward the same number and wash out the only
  difference this exists to see.
- **It is one reader's own ratings and nobody else's.** No aggregate, no crowd, no chart.

### Alternatives that lost
**Measure every edge out of every rated show, not just the ones landing back inside the set.**
This gives all ten of a ten-rating user's shows a composition instead of six, so it looks like
more signal. It is not: the far end of those edges is a show the reader has never judged, so the
rating on the near end is being credited to a connection the reader never evaluated. It also
costs an order of magnitude more work, indexing about 130 shows instead of a dozen.

**Correlate rating against cast share directly.** Same numbers in the limit, but it introduces a
second statistical idiom into a module whose whole argument is that it is one linear, inspectable
model. The weighted mean already in use for tags answers the question and reads the same way as
its neighbours.

**Let the reader set it themselves.** Out of scope by decision, deferred to v2. A control is a
different product question from a learned signal, and shipping the control first would have made
the learned version unnecessary rather than informing it.

## After Action Review
It works, and on today's database it correctly declines to do anything. The ten-rating user gets
no lean because they rated everything highly; the ratingless user gets no lean because they have
no ratings. Both are the honest answer. A synthetic fifty-rating user with genuinely split
opinions clears all three gates and produces cast 0.662 against crew 0.141.

Cost is bounded rather than proportional. Reading a reader's edges is capped at the strongest 60,
so the work stops growing after that: four queries, 174ms for a reader who has rated the entire
464-show catalog. Along the way `role_index` grew a bulk form, `role_indexes`, which cut the show
detail page from 26 queries to 2 for the same work. That bulk form was verified against the
per-show original across all 464 shows and all 280,229 person-show entries, with zero
differences.

Tested in `ConnectionTypePreferenceTests` and `ConnectionTypeNamingTests`: cold start, anonymous,
a cast-leaning reader, a crew-leaning reader, a low rating signing the lean negative, and each of
the three gates refusing separately. `CalloutOrderingTests` freezes which block opens the
sentence, including that an unearned lean produces the byte-identical default sentence.
`CrewRoleCollapseTests` freezes the role collapse. `RoleIndexesBulkTests` freezes the bulk
equivalence and the query count. The suite went from 248 to 286.

Both open items from the first cut are closed.

Ordering the prose blocks was the first, and it is done, above. The second was that a crew lean
made issue #4's director repetition worse by naming more directors: a callout could read
"director Bryan Spicer directed 12 episodes, director Kevin Hooks directed four episodes,
director Dwight H. Little directed two episodes and director Milan Cheylov directed 18 episodes".
Now only the strongest holder of a role keeps the full clause and the rest collapse behind it,
which is what the cast side has always done with "and Leslie Hope and Carlos Bernard appear too".
Grouping is by prose noun rather than raw TMDb job, so "Original Music Composer" and "Composer"
collapse together instead of saying "composer" twice. Across all 4,014 edges, zero callouts now
repeat a role noun. The cost is the episode counts of the collapsed members, which are the least
interesting numbers in the sentence.

**The thresholds are first estimates, not measurements.** Four edges, 1.0 of mass, half a star.
Each is argued from what the number means rather than fitted to observed behaviour, because with
two users there is nothing to fit against. They should be revisited once more than one person has
rated enough connected shows to clear them.
