# 9. Side Quests: strong edges into genres you have not shown you like

## Context
The home page has three rows. Top Picks is the signed-in user's own rated shows ranked by
lift over a global baseline ([#15](https://github.com/patricio-quezada/tvlens/issues/15)).
Recently added is the catalog. Side Quests was a stub: it rendered only for signed-in users,
it was always empty, and its copy promised it would "unlock once you've rated a handful of
shows". My demo note
([#10](https://github.com/patricio-quezada/tvlens/issues/10)) was blunt about that: the row
should load even for a user who has rated nothing.

What the row is *for* is the harder half. As I put it at the time, Side Quests is "shows on the
fringes of the recommender system, if you want to be spontaneous but stay within a taste
profile. The best analogy is ordering something at a restaurant that you are unsure of but want
to try anyways." So a pick has to be plausibly likeable and clearly not something you would
have reached for yourself. Not random, and not popular. On review I sharpened it further: a
side quest is a **surprise**, "maybe a genre a user didn't think they'd like."

Two existing constraints bound the answer. Nothing in TVLens is ever a popularity chart
([ADR-05](05-no-signal-fallback-ladder.md)), which rules out the easy fillers. And
[ADR-08](08-layer2-personalized-reranking.md) fixed the shape of personalization here: Layer
2 re-ranks Layer 1's stored list per user, it never scores shows a second time.

The measurements below are against the live catalog: 100 shows, and 1041 edges in the
materialized Layer 1 store ([ADR-07](07-materialized-recommendations.md)), all of them on the
"weighted" rung of the ladder. Edge scores run 0.00124 to 14.79, with quartiles at 0.176,
0.439 and 1.013. Genres are thin and lopsided: 2.45 per show on average, and Drama alone is
on 66 of the 100 shows, ahead of Action & Adventure 37, Crime 35, Sci-Fi & Fantasy 29,
Comedy 22, Mystery 19 and Animation 16.

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
stub with no definition; once the definition is "surprising relative to what you have
demonstrated you like", loading it for a user who has demonstrated nothing is not a feature, it
is a contradiction. The row is now gated, and the page says so in as many words.

The mechanism survived this. A side quest is still a strong Layer 1 edge reaching somewhere the
user has not been, and it survived the second revision below too; what changed both times was
who the row is for and how it is ordered. The file name stays across both, so every reference
to this record from the code and from the other records still resolves.

### How it changed again: the row was the recommendation row wearing a different title
The first amendment fixed *who* the row is for. It left the ordering untouched, and
with the row on the page that turned out to be the larger problem. Looking at my
own account:

> side quests seem to be showing the top recommendations of the three shows I have rated. Is
> that what we want? I thought we wanted side quests to be more like "edge cases" but bound by
> our recommendations. Think of it as a second-degree connection to the fourth-degree of Kevin
> Bacon.

That was right, and the cause was structural rather than a tuning miss. There were two faults
in it.

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
you have seen is a real shared-people connection, and treating it as a dead end throws away the
graph's most reliable edges.

**A show that many seeds reach sinks.** Divided by `(seeds that reached it) **
SIDE_QUEST_CENTRALITY_EXPONENT`. This is the term that most directly answers "edge cases bound
by our recommendations": a candidate every one of your favorites points at is at the *centre*
of your taste, and centrality is measurable where peripherality was previously only implied.

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
Crime-tagged show score as fully novel, and a seed whose neighbourhood is one cluster can fill
most of the row with that cluster. Genre affinity has more resolution than has-genre /
has-not, and Layer 2 already keeps a signed number per genre.

## Decision
**A side quest is a strong Layer 1 connection, out of a show this user rated highly, that lands
in genres this user has never rated highly and that few of their favorites point at.** Four
parts, in the order the code applies them.

**Seeds.** The shows the user rated at or above 4.0. That is the same "high" line the home
page already uses to glow a favorite genre and that ADR-08 personalizes from, so every
feature on the page agrees about what "you like this" means.

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
show only ever falls down that chain.

### What the numbers say
**These figures predate the second amendment.** They measured the one-hop walk ordered by
`score x novelty`, and they remain here because what they establish still holds: the row is
personal rather than global, it is not a chart, and it is honestly short at 100 shows. Row
sizes and specific orderings will have moved. Re-running the samplers against the current rule
belongs with [#20](https://github.com/patricio-quezada/tvlens/issues/20).

There is one rating in the development database, so I measured the row against 4000 synthetic
three-seed users per sampler: **uniform**, three shows drawn at random, and **coherent**, three
shows drawn from a single genre, which is the harder and more realistic case because a real
taste is not random.

Row size, coherent seeds: median 6 picks, mean 5.7, empty for 3 percent of users, at least 3
picks for 76 percent, at least 5 for 60 percent. Uniform seeds run a little fuller: median 7,
empty 3 percent. **The row is usually half a strip rather than a full one, and that is the
honest answer at 100 shows.** It fills to all 12 for only 3 percent of coherent users.

The picks are strong edges, not tail scrapings: median Layer 1 score 0.580 against a store
median of 0.439, with 20 percent below the store's bottom quartile (0.176). Median novelty is
0.50, meaning the typical pick is half made of genres this user has never rated highly, and 8
percent of picks are entirely so.

It is not one list. Across coherent users the row reaches 79 of the 100 shows and 59 different
shows appear in the lead position. Two random users' rows overlap by a mean Jaccard of 0.08,
median 0.00, and are identical 2.8 percent of the time (those are users whose sampled seed
triples happened to coincide). Under the withdrawn cold-start path that number was 100
percent by construction.

It is not a chart. Mean Spearman correlation between a row's order and its members'
popularity rank is 0.156, and against vote_average rank 0.028. Both are noise.

### Tags are not available, and would sharpen this
I described the surprise as running on "connections and tags". The connections are Layer 1, and
the row uses them. The tags are not: `Tag` and `ShowTag` are both empty, 0 rows, so genre is
the only categorical signal the catalog has today. Genre is coarse for this job, as the
Drama-on-66-shows number shows. Once the ingest lands tags, the same shape works with a finer
vocabulary: the novelty share would run over tags as well as genres, which would let the row
tell "a workplace comedy you have not tried" apart from "a comedy", and would push the
fully-novel band above 8 percent of picks. That is a later decision, not this one.

### One hazard this touched
`Show.Meta.ordering = ["-popularity"]` means any queryset that forgets an explicit
`order_by` silently becomes the popularity ranking ADR-05 forbids. Every queryset on the Side
Quests path is explicitly ordered and says so in a comment. Whether that default should
change repo-wide is a separate decision with a wider blast radius and is not settled here.

## After Action Review
Tested in `shows/tests.py::SideQuestsTests`, which freezes the definition: the row locks below
three high ratings, anonymous visitors get neither row nor copy, every pick lands in a genre
the user has never rated highly, the walk refuses the graph's strongest edge when it is more of
the same, distance can beat a stronger edge and cannot win on its own, the walk covers only the
strong half of a seed's list and only the user's own favorites, and an unlocked user with
nothing new gets no section rather than the locked copy.

`SideQuestsRankingTests` freezes the second amendment: a blockbuster edge no longer outranks a
novel one while strength still separates two equally novel shows, a show two hops out can be a
pick and loses to an identical show one hop in, a watched show is never a pick and still
carries the walk, a show every seed reaches ranks below one that only a single seed found, and
the surprise arithmetic appears once, written out against a known pick.