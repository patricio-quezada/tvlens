---
adr: 9
title: "Side Quests: strong connections into genres a user has not rated highly"
status: amended
date: 2026-08-21
tags:
  - adr
relates:
  - "[[05-no-signal-fallback-ladder]]"
  - "[[07-materialized-recommendations]]"
  - "[[08-layer2-personalized-reranking]]"
---
# 9. Side Quests: strong connections into genres a user has not rated highly

**A side quest is a show a user would plausibly like but would not have reached for, and
surprise only means anything measured against what someone has demonstrated. The row is built
from that user's own highly-rated shows, walks two hops out at a discount, and ranks by
strength times novelty times how few of their favorites reach it.**

## Context
Side Quests shipped as a stub. It rendered for signed-in users only, it was always empty, and
its copy promised it would "unlock once you've rated a handful of shows".
[#10](https://github.com/patricio-quezada/tvlens/issues/10) asked for it to load even for a
user who had rated nothing.

That was the easy half. What the row is *for* was harder.

I decided that a side quest is a **surprise**: a show the user would plausibly like, would not
have reached for alone, and that lands in a genre they did not expect to like. The closest
analogy is ordering an unfamiliar dish at a restaurant on the chance it is good. Not random,
because random is noise. Not popular, because everyone already knows what is popular.

Two existing rules bound the answer. Nothing in TVLens is ever a popularity chart
([ADR-05](05-no-signal-fallback-ladder.md)), which rules out the easy fillers. And
[ADR-08](08-layer2-personalized-reranking.md) fixed the shape of personalization: Layer 2
re-ranks Layer 1's stored list per user and never scores shows a second time.

### How this decision changed: surprise needs an expectation to violate
The first version of this row shipped with a cold-start path: a visitor with no ratings, or
no account at all, saw the catalog's strongest cross-genre edges. That path is now removed,
for two reasons that only became visible with the row on the page.

**A global row is surprising relative to nothing.** Surprise is not a property of a show. It
is a relation between a show and what someone expected, and a person who has rated nothing
has demonstrated no expectation. What the cold-start row actually produced was one fixed
list, identical for every visitor, with nothing in it derived from the person looking at it.
That is the thing this project keeps refusing (ADR-05, ADR-07, ADR-08): not because it ranked
by popularity, it did not, but because a single list served to everyone is a chart whatever
it is sorted by.

**"The catalog's strongest cross-genre edges" is not the fringe.** The strongest of them scores
inside the top 10 percent of the whole graph. Unexpected in category, but the opposite of
peripheral. Sorting the cross-genre subset by strength finds the loudest edges that happen to
cross a line, not the quiet ones.

This also overturns the literal ask in #10, "they should load even if the user has not rated
any shows". Deliberately, and my own call. I wrote #10 during a demo when the row was an empty
stub with no definition; once the definition is surprise measured against demonstrated taste,
loading the row for a user who has demonstrated nothing is not a feature, it is a
contradiction. The row is now gated, and the page says so in as many words.

The mechanism survived this. A side quest is still a strong Layer 1 edge reaching somewhere the
user has not been, and it survived the second revision below too; what changed both times was
who the row is for and how it is ordered. The file name stays across both, so every reference
to this record from the code and from the other records still resolves.

### How it changed again: the row was the recommendation row wearing a different title
The first amendment fixed *who* the row is for. It left the ordering untouched, and
with the row on the page that turned out to be the larger problem: the row was returning the
top recommendations of the shows I had rated, when what it should return is edge cases still
bound by those recommendations, a second-degree connection rather than a first. The cause was
structural rather than a tuning miss, and there were two faults in it.

**The candidate pool was the recommendation pool.** Walking ranks 0 through 5 out of the
seeds *is* what a "more like this" row shows. Surprise was then applied only as a re-sort over
that pool, so the row could re-order those shows but could never leave them. Measured on the
development database with three seeds: **11 of 11 picks came from the first-degree pool.** The
overlap was 100 percent by construction, not by coincidence. Graph distance was not one of the
row's axes; it was a constant, pinned at its minimum.

**Strength drowned novelty.** The order was `score x novelty`, and the docstring claimed
"both halves have to be there". They did not have comparable range. Across one real candidate
set, Layer 1 scores ran 0.42 to 5.76, about 14x, while novelty is a share and spans at most
5x in practice. The bigger term decided the row. The lead pick, Person of Interest, had a
novelty of 0.25 and sat first purely on a 5.76 edge: the top "surprise" was the top
recommendation.

#### What changed
**Strength is log-compressed.** `log1p(score)` puts strength on novelty's order of magnitude,
which is the only condition under which multiplying them means anything. This is the smallest
of the three changes and it fixes the symptom that was actually visible.

**The walk goes a second hop, at a discount.** A two-hop path scores at its weakest link,
multiplied by `SIDE_QUEST_HOP_DECAY`, so distance is earned rather than assumed. Shows the user
has already watched stay in the walk as *bridges* even though they can never be picks: a show
the user has seen is a real shared-people connection, and treating it as a dead end throws away
the graph's most reliable edges.

**A show that many seeds reach sinks.** Divided by `(seeds that reached it) **
SIDE_QUEST_CENTRALITY_EXPONENT`. This is the term that most directly answers "edge cases bound
by our recommendations": a candidate every one of a user's favorites points at sits at the
*center* of that taste, and centrality is measurable where peripherality was previously only
implied.

#### What it did
Same user, same three seeds, before and after:

| | Before | After |
|---|---|---|
| picks from the first-degree recommendation pool | 11 of 11 | 9 of 12 |
| rank of Person of Interest (edge 5.76, novelty 0.25) | 1st | 5th |

Three of twelve picks are now shows a recommendation row would never have surfaced, and the
strongest edge in the pool no longer leads simply because it is the strongest edge.

#### What this change deliberately does not settle
I fitted every constant here against a 100-show catalog in which two hops already reach 43
percent of everything. `SIDE_QUEST_HOP_DECAY = 0.5` is a round number chosen where hop-2
candidates were plentiful, and whether it is too harsh or too generous is not answerable from
this data. Deferred to
[#20](https://github.com/patricio-quezada/tvlens/issues/20), together with a limitation the
same measurement exposed: novelty is binary, so a user with no Crime rating sees *every*
Crime-tagged show score as fully novel, and a seed whose neighborhood is one cluster can fill
most of the row with that cluster. Genre affinity has more resolution than has-genre /
has-not, and Layer 2 already keeps a signed number per genre.

## Decision
**A side quest is a strong Layer 1 connection, out of a show this user rated highly, that lands
in genres this user has never rated highly and that few of their favorites point at.** Four
parts, in the order the code applies them.

**Seeds.** The shows the user rated at or above 4.0. That is the same "high" line the home page
already uses to glow a favorite genre and that ADR-08 personalizes from, so every feature on
the page agrees about what liking a show means.

**The gate: three seeds, or the row stays locked.** Below three the row does not render at
all, and a signed-in user sees one line of copy telling them how to unlock it. One seed says
which show a person watched, not which genres they lean toward; nothing can be measured as
surprising against a single point. Three is the smallest history in which a genre can be seen
to recur, and therefore the smallest history in which its *absence* means something.
Anonymous visitors get no row and no copy: they cannot rate anything without an account, so
the unlock instruction would be a dead end.

**The walk: the strong half of each seed's stored list, and then the strong half of the lists
belonging to what that reached.** Ranks 0 through 5 of the 12 Layer 1 keeps per show (ADR-07),
followed one hop further out. The rank cap holds at every hop, because a side quest has to be a
*confident* connection and the weak tail of a list is mostly coincidence, so a strange genre
found down there is noise wearing a surprise costume. But one hop is exactly the seed's own
recommendation list, so a one-hop row can re-order that pool and never leave it. The second hop
is what gives distance somewhere to vary. A show the user has already watched is never a pick
and always a bridge: it is a real connection, not a dead end.

**Surprise: the share of a candidate's genres the user has no positive history with.**
Collect the genres of the seeds; call that the demonstrated taste. For each candidate,
novelty is the number of its genres outside that set divided by the number of genres it has.
A candidate with novelty 0 is not a side quest, and the walk drops it however strong its edge.

**The order is strength x novelty x centrality.** Strength is `log1p` of the reaching edge's
Layer 1 score, taken at the weakest link of the path and decayed once per extra hop. Novelty
is the share above. Centrality divides by the square root of how many seeds reached the
candidate. All three have to be present: a blockbuster edge into more of the same sinks on
novelty, a thin edge into a strange genre sinks on strength, and a show every favorite points
at sinks on centrality. The log is not cosmetic. Raw Layer 1 scores span more than an order of
magnitude while novelty is a share bounded at 1, so a plain product let the strongest edge in
the pool win on strength alone, which is precisely how the row came to read as a
recommendation list.

The only new number is a multiplier on Layer 1's own score. Nothing scores a show twice and no
second engine runs over the catalog, which keeps this inside ADR-08's rule that Layer 2
re-ranks rather than re-scores.

Shows the user has already watched never appear, watched covering rated (ADR-08), and neither
does anything the home page has already used in Top Picks.

**Three render states, and the third one matters.** Cards when the walk found something; the
locked copy when the user is under three seeds; and *nothing at all* when an unlocked user's
own seeds happen to reach nothing new. Showing "rate three shows to unlock" to someone who
has already rated three shows would be a lie, and it happens to about 3 percent of users in
simulation, so it is a real state rather than a theoretical one.

**The dedupe chain is unchanged: Top Picks, then Side Quests, then Recently added.** One show,
one row. The priority is by how *personal* a row is, and it deliberately does not match render
order: Side Quests sits below Recently added on the page but claims its shows before it. A
show only ever falls down that chain. Watch Next has since joined the chain (#24), claiming
after Side Quests and before Recently added; the comment in `views.index` records why it
yields to a row with the smaller pool.

### What the numbers established, and what has since moved
Measured in August 2026 against 4000 synthetic three-seed users on a 100-show catalog, before
the genre gate was graded and before the catalog was filtered and pruned to 248. **The figures
are gone; three findings from them still hold and are the reason this row exists:**

- **It is personal, not global.** Two random users' rows overlapped by a mean Jaccard of 0.08,
  median 0.00. Under the withdrawn cold-start path that number was 100% by construction.
- **It is not a chart.** Mean Spearman correlation between a row's order and its members'
  popularity rank was 0.156, and 0.028 against vote_average. Both noise.
- **It is honestly short.** Median 6 picks of a possible 12, and it filled completely for 3%
  of users. A half strip is the truthful answer when the catalog is small.

Re-measuring against the current rule and the current catalog is worth doing before the demo.

### Tags exist now, and would still sharpen this
When this was written `Tag` and `ShowTag` held 0 rows, so genre was the only categorical
signal. ADR-14 landed tags, which held 7 tags and 9 applications at the time,
enough to exist and not enough to run novelty over. Genre remains coarse for this job. Once tags are populated, the same shape
works with a finer vocabulary: novelty would run over tags as well as genres, letting the row
tell "a workplace comedy you have not tried" from "a comedy". Still a later decision.

### One hazard this touched
`Show.Meta.ordering` was `["-popularity"]` at the time, which meant any queryset that forgot
an explicit `order_by` silently became the popularity ranking ADR-05 forbids. Every queryset
on the Side Quests path is explicitly ordered and says so in a comment. The default has since
changed to `["name"]`, deliberately; the comment on `Show.Meta` records the reasoning.

## After Action Review
`shows/tests.py::SideQuestsTests` freezes the definition:

- the row locks below three high ratings
- anonymous visitors get neither the row nor the copy
- every pick lands in a genre the user has never rated highly
- the walk refuses the graph's strongest edge when it is more of the same
- distance can beat a stronger edge and cannot win on its own
- the walk covers only the strong half of a seed's list, and only the user's own favorites
- an unlocked user with nothing new gets no section rather than the locked copy

`SideQuestsRankingTests` freezes the second revision:

- a blockbuster edge no longer outranks a novel one, while strength still separates two equally
  novel shows
- a show two hops out can be a pick, and loses to an identical show one hop in
- a watched show is never a pick and still carries the walk
- a show every seed reaches ranks below one that only a single seed found
- the surprise arithmetic appears once, written out against a known pick

## Note, 2026-08-26: the hop decay is tilted, and left alone
**status: accepted 2026-09-04**

[ADR-04](04-episode-weighted-people-recommender.md) raised each shared person's episode share to
`INVOLVEMENT_EXPONENT = 1.375` before summing. That changes the input to `math.log1p(score)`,
and this note records the effect so it is not rediscovered later as a mystery.

`log1p` is concave, so it compresses large scores while the exponent crushes small ones. On the
rank<=5 edges the walk actually uses, the maximum strength falls by 3.4% while the median falls
by 43%. Strong edges keep their strength and weak edges lose theirs, so a two-hop path through
two strong edges competes better against a one-hop path through a weak one than it used to.

Measured on the rebuilt catalog, sampling from that pool: the probability that a two-hop path
out-scores a one-hop path rises from 22.5% to 26.1%, a factor of 1.16. Restoring the old balance
would mean `SIDE_QUEST_HOP_DECAY = 0.33` rather than 0.5.

**Left at 0.5 deliberately.** This ADR already flags the constant as needing a re-fit at real
catalog scale, tracked in #20, and it was tuned against a 100-show catalog that no longer
exists. Re-fitting it inside the rescoring change would mix two adjustments and make neither
measurable. Side Quests was also expected to give way to Watch Next as the homepage row.
Watch Next has since shipped (#24) and leads the page, but Side Quests stayed below it, so
these constants are still live.

The direction of the tilt is toward novelty, which is the axis this row exists to serve, so 3.6
percentage points is not a regression. It is written down here so the next person to touch the
constant knows part of the drift came from Layer 1 rather than from this row.

`ADR-04` records the same measurement from the other side.

## Amendment, 2026-08-27: the genre gate is graded, not binary

`demonstrated` was a set, so one seed was enough to disqualify a genre forever.
Measured against the live catalog with ten seeds:

| Genre | Seeds carrying it | Disqualifying force |
|---|---|---|
| Drama | 9 of 10 | total |
| Sci-Fi & Fantasy | 5 of 10 | total |
| Crime | 3 of 10 | total |
| Action & Adventure | 2 of 10 | total |
| Mystery | 1 of 10 | total |
| Western | 1 of 10 | total |

One show tagged Western removed every Western in the catalog with the same force
as nine Drama seeds. The cost was not marginal: **82 of 104 reachable candidates
were dropped**, and what survived was ten comedies out of twelve, not because
comedy suited the reader but because comedy was what was left. The rule also got
worse with use, since every genre a reader demonstrated was removed permanently.

Familiarity is now a share of the seeds rather than a membership test, and
novelty is the mean of `1 - familiarity` across the candidate's genres, raised to
`SIDE_QUEST_GENRE_EXPONENT`.

**The hard drop is gone, but its behavior is not.** A candidate whose every
genre sits at familiarity 1.0 scores exactly 0.0 and can never be chosen, so the
old cliff survives as the limit case of the formula rather than as a branch
beside it.

### Choosing the exponent

Grading the gate pulls this row toward Watch Next, which is what the exponent
exists to control. Measured across seven values on the real catalog:

| Exponent | New entrants (of 12) | Shared with Watch Next |
|---|---|---|
| 0.5 | 9 | 7 |
| 1.0 | 7 | 5 |
| **1.5** | **5** | **3** |
| 2.0 | 4 | 2 |
| 3.0 | 4 | 1 |
| 4.0 | 4 | 1 |

Below 1.5 the surprise row fills with the recommendation row. Above 2.0 it
collapses back to the comedy list it started as. **1.5** was chosen: 7 comedies
of 12 against 2.0's 8, the same genre spread, and it is where a genre brushed
once can reach the row again. Criminal Minds and The Boroughs, both Mystery at
1 of 10 seeds, enter at this value and at no higher one.

### Rejected the same day: removing the gate entirely

Letting distance carry the row alone was measured and rejected. Without the
genre term the top seven were MobLand, Lioness, Hawaii Five-0, Law & Order:
Criminal Intent, Lanterns, Law & Order: SVU and Law & Order. Distance in this
graph correlates with sharing people, and people cluster by genre, so walking
further returns a different show rather than a different kind of show. Dropping
the gate did not make the row surprising. It made it safe.
