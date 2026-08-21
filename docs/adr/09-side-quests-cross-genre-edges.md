# 9. Side Quests: the catalog's strongest cross-genre edges

## Context
The home page has three rows. Top Picks is the signed-in user's own rated shows ranked by
lift over a global baseline ([#15](https://github.com/patricio-quezada/tvlens/issues/15)).
Recently added is the catalog. Side Quests was a stub: it rendered only for signed-in users,
it was always empty, and its copy promised it would "unlock once you've rated a handful of
shows". Patricio's demo note
([#10](https://github.com/patricio-quezada/tvlens/issues/10)) was blunt about that: the row
should load even for a user who has rated nothing.

What the row is *for* is the harder half. In Patricio's words, Side Quests is "shows on the
fringes of the recommender system, if you want to be spontaneous but stay within a taste
profile. The best analogy is ordering something at a restaurant that you are unsure of but
want to try anyways." So a pick has to be plausibly likeable and clearly not something you
would have reached for yourself. Not random, and not popular.

Two existing constraints bound the answer. Nothing in TVLens is ever a popularity chart
([ADR-05](05-no-signal-fallback-ladder.md)), which rules out the easy fillers. And
[ADR-08](08-layer2-personalized-reranking.md) fixed the shape of personalization here: Layer
2 re-ranks Layer 1's stored list per user, it never scores shows a second time, and it has to
work from zero ratings under a neutral prior and personalize away from that prior as ratings
arrive.

The measurements below are against the live catalog: 100 shows, and 1041 edges in the
materialized Layer 1 store ([ADR-07](07-materialized-recommendations.md)), all of them on the
"weighted" rung of the ladder.

## Decision
**Side Quests is the subset of the Layer 1 graph whose two ends share no genre at all.** A
"cross-genre edge" is a stored edge between two shows with no genre in common: the shared
people say the two shows are connected, and the genre labels say they are not the same kind
of thing. That gap is the side quest. The row is the strongest such edges, strongest first,
one card per show.

There is no new score. The edge is Layer 1's own episode-weighted shared-people score
([ADR-04](04-episode-weighted-people-recommender.md)), read straight out of the store. All
Side Quests does is filter the graph and read it in a different order, which is what keeps it
inside ADR-08's rule that Layer 2 re-ranks rather than re-scores.

**Cold start is the whole catalog's cross-genre edges; ratings seed the walk.** With no
ratings, whoever is looking gets the catalog's strongest cross-genre edges. Once the user has
rated a show at 4.0 or higher, the walk starts from those shows instead: the cross-genre
neighbors of what they already like. That mirrors ADR-08 exactly, a neutral prior at cold
start that personalizes away as ratings arrive. The 4.0 floor is the same "high" line the
home page already uses to glow a favorite genre, so the two features agree about what "you
like this" means.

A seeded row is topped up from the catalog-wide walk when the user's own seeds run dry, and a
seed often does run dry: a show with a broad genre list, like Game of Thrones carrying Drama,
Action and Sci-Fi at once, has almost no edge that escapes all three. The seeded picks always
come first, even when a top-up edge scores higher, because a neighbor of a show this user
liked is a better side quest than a stronger edge between two shows they have never touched.
Shows the user has already watched never appear.

**Anonymous visitors get the row.** The cold-start path needs no ratings, so there is nothing
to withhold. Top Picks stays signed-in only, because it is literally the user's own ratings.

**The dedupe chain is Top Picks, then Side Quests, then Recently added.** One show, one row.
The priority is by how *personal* a row is, and it deliberately does not match render order:
Side Quests sits below Recently added on the page but claims its shows before it. A show only
ever falls down that chain. (Top Picks winning over Side Quests is belt and braces today,
since every Top Pick is a rated show and Side Quests already excludes everything the user has
watched, but it is written out so the chain stays correct if either rule changes.)

### What the numbers say
127 of the 1041 stored edges, 12.2 percent, cross a genre line. They touch 45 distinct shows
as targets and 37 as sources, so a bit under half the catalog is reachable as a side quest.
Their scores run from 1.865 down to 0.00124 with a median of 0.224, against a median of 0.439
across the whole store; none of them is a zero-score edge, so every pick rests on real shared
people rather than a fallback rung.

The cold-start row of 12 is not a chart. The popularity ranks of its members are 1, 7, 11, 15,
22, 29, 33, 47, 52, 66, 87 and 95, spanning nearly the whole catalog. The correlation between
the row's order and those popularity ranks is -0.177, and against vote_average rank it is
0.063: both are noise. Only 3 of the 12 are in the catalog's top 12 by popularity and only 2
are in its top 12 by rating.

The picks read the way the row is supposed to read. Better Call Saul next to The Boys, on nine
shared people. Westworld next to Lost, on twelve. Modern Family reaching Bones. Breaking Bad
reaching The Mandalorian.

### Alternatives, and why they lost
**A. Score the whole catalog a second time per user and take the middle band.** Rejected on
principle before it was measured, because it breaks ADR-08 twice over. ADR-08 says Layer 2
re-ranks Layer 1's list and does not send the request through a separate engine that scores
every show from scratch; a middle-band pass over all 100 shows is exactly that second engine.
And at cold start it has no user signal at all, so it collapses to the shared quality prior,
which is one fixed ordering of the whole catalog shown to every visitor. That is a chart with
a different name.

**B. Random unwatched shows.** Patricio ruled it out by intent, and the data agrees. Only 45
of the 100 shows carry any cross-genre Layer 1 edge, so a random pick has a 45 percent chance
of being connected to anything at all across a genre line. The other 55 percent are there for
no reason, and a row of no-reason cards cannot be explained, which is the thing ADR-08 says
the product is.

**C. The long tail, meaning the lowest-popularity shows.** This is a popularity chart read
from the bottom, and ADR-05 forbids ranking by popularity in either direction. It also does
not find the fringe: only 4 of the bottom 12 by popularity carry a cross-genre edge.

**E. The weak tail of every show's own Layer 1 list, ranks 8 and below.** The intuition was
that "fringe" means "weak edge". It measures badly: those 313 edges are only 16 percent
cross-genre, and their median score is 0.255 against 1.154 for the top three ranks. So the
tail is mostly more of the same genre, held together by less evidence. Weakness is the wrong
axis. A side quest should be a *confident* edge into a different kind of show, not a shaky
edge into the same kind.

### One hazard this touched
`Show.Meta.ordering = ["-popularity"]` means any queryset that forgets an explicit
`order_by` silently becomes the popularity ranking ADR-05 forbids. Every queryset on the Side
Quests path is now explicitly ordered and says so in a comment. Whether that default should
change repo-wide is a separate decision with a wider blast radius and is not settled here.

## After Action Review
Pending. Only Patricio can say whether the row does what he asked for, which is that a pick
feels like something you were unsure of but wanted to try. Fill this in after using it against
the local demo. Two things worth watching:

- The catalog-wide cold-start row repeats both ends of a symmetric edge, so Better Call Saul
  and The Boys sit next to each other, and Westworld next to Lost. Both ends are honest side
  quests, but the pairing may read as a duplicate on the page.
- The row's promise copy is still a placeholder. The old line, "based on what you rate highly,
  unlocks once you've rated a handful", is now wrong on both counts.

Tested in `shows/tests.py::SideQuestsTests`, which freezes the four things that make this row
what it is: every pick crosses a genre line, the cold-start order is not a popularity ordering,
anonymous visitors get the row, and a high rating takes over the walk.
