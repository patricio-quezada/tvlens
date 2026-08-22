# 9. Side Quests: strong edges into genres you have not shown you like

> **Amended 2026-08-21.** The original decision had two halves. The mechanism, a side
> quest is a strong Layer 1 edge that crosses a genre line, survives. The cold-start half,
> where a visitor with no ratings saw the catalog's strongest cross-genre edges, is
> withdrawn. Section "Amendment" says what changed and why; the Decision below is the
> current rule, not the original one. The file name is kept so existing references still
> resolve.
>
> **Amended again 2026-08-22.** The mechanism survives a second time; the *ranking* does not.
> The one-hop walk made the candidate pool identical to the seeds' own recommendation lists,
> and multiplying raw edge scores by novelty let the strongest edge in that pool win on
> strength alone. Section "Second amendment" says what changed and why. The Decision below is
> the current rule.

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
would have reached for yourself. Not random, and not popular. On review he sharpened it
further: a side quest is a **surprise**, "maybe a genre a user didn't think they'd like."

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

## Amendment: surprise needs an expectation to violate
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

**"The catalog's strongest cross-genre edges" is not the fringe.** Better Call Saul to The
Boys scores 1.865, which is inside the top 10 percent of the whole graph. Unexpected in
category, but the opposite of peripheral. Sorting the cross-genre subset by strength finds
the loudest edges that happen to cross a line, not the quiet ones.

This also overturns the literal ask in #10, "they should load even if the user has not rated
any shows". Deliberately, and Patricio's call. #10 was written during a demo when the row was
an empty stub with no definition; once the definition is "surprising relative to what you
have demonstrated you like", loading it for a user who has demonstrated nothing is not a
feature, it is a contradiction. The row is now gated, and the page says so in as many words.

## Second amendment: the row was the recommendation row wearing a different title
The first amendment fixed *who* the row is for. It left *how it is ordered* untouched, and
with the row on the page that turned out to be the larger problem. Patricio, looking at his
own account:

> side quests seem to be showing the top recommendations of the three shows I have rated. Is
> that what we want? I thought we wanted side quests to be more like "edge cases" but bound by
> our recommendations. Think of it as a second-degree connection to the fourth-degree of Kevin
> Bacon.

He was right, and it was structural rather than a tuning miss. Two faults.

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

### What changed
**Strength is log-compressed.** `log1p(score)` puts strength on novelty's order of magnitude,
which is the only condition under which multiplying them means anything. This is the smallest
of the three changes and it fixes the symptom that was actually visible.

**The walk goes a second hop, at a discount.** A two-hop path is scored at its weakest link
and multiplied by `SIDE_QUEST_HOP_DECAY`, so distance is earned rather than assumed. Shows the
user has already watched stay in the walk as *bridges* even though they can never be picks: a
show you have seen is a real shared-people connection, and treating it as a dead end throws
away the graph's most reliable edges.

**A show that many seeds reach is pushed down.** Divided by `(seeds that reached it) **
SIDE_QUEST_CENTRALITY_EXPONENT`. This is the term that most directly answers "edge cases bound
by our recommendations": a candidate every one of your favorites points at is at the *centre*
of your taste, and centrality is measurable where peripherality was previously only implied.

### What it did
Same user, same three seeds, before and after:

| | Before | After |
|---|---|---|
| picks from the first-degree recommendation pool | 11 of 11 | 9 of 12 |
| rank of Person of Interest (edge 5.76, novelty 0.25) | 1st | 5th |

Three of twelve picks are now shows a recommendation row would never have surfaced, and the
strongest edge in the pool no longer leads simply because it is the strongest edge.

### What this amendment deliberately does not settle
Every constant here was fitted against a 100-show catalog in which two hops already reach 43
percent of everything. `SIDE_QUEST_HOP_DECAY = 0.5` is a round number chosen where hop-2
candidates were plentiful, and whether it is too harsh or too generous is not answerable from
this data. Deferred to
[#20](https://github.com/patricio-quezada/tvlens/issues/20), together with a limitation the
same measurement exposed: novelty is binary, so a user with no Crime rating sees *every*
Crime-tagged show score as fully novel, and a seed whose neighbourhood is one cluster can fill
most of the row with that cluster. Genre affinity has more resolution than has-genre /
has-not, and Layer 2 already keeps a signed number per genre.

### Alternatives considered for this amendment
**Normalize strength against the strongest edge in the user's own pool.** Scale-free and
tempting, but it makes a pick's score depend on which *other* shows happened to be in the pool,
so the same show scores differently for two users with the same edge to it. `log1p` is a fixed
function of the edge and nothing else.

**Divide by the seed count outright rather than its square root.** A plain reciprocal costs a
show reached by three seeds two thirds of its score, which disqualifies rather than
discounts. Being reached twice should cost something; it should not be fatal.

**Go three or more hops.** Untested and probably not useful at this catalog size, where two
hops already reach 43 percent of the shows. `SIDE_QUEST_MAX_HOPS` is a named constant so this
is a measurement away rather than a rewrite, and it belongs with #20.

**Fix the ordering and leave the walk at one hop.** This would have addressed the visible
symptom, since log compression alone moves Person of Interest off the top. It would not have
addressed what Patricio actually identified: a row that can only ever re-order the
recommendation list is not a different row.

## Decision
**A side quest is a strong Layer 1 connection, out of a show this user rated highly, that
lands in genres this user has never rated highly and that few of their favorites point at.** Four parts, in the order the code applies them.

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

**The walk: the strong half of each seed's stored list, and then the strong half of the
lists belonging to what that reached.** Ranks 0 through 5 of the 12 Layer 1 keeps per show
(ADR-07), followed one hop further out. The rank cap holds at every hop, because a side quest
has to be a *confident* connection and the weak tail of a list is mostly coincidence, so a
strange genre found down there is noise wearing a surprise costume (alternative E below). But
one hop is exactly the seed's own recommendation list, so a one-hop row can re-order that pool
and never leave it. The second hop is what gives distance somewhere to vary. A show the user
has already watched is never a pick and always a bridge: it is a real connection, not a dead
end.

**Surprise: the share of a candidate's genres the user has no positive history with.**
Collect the genres of the seeds; call that the demonstrated taste. For each candidate,
novelty is the number of its genres outside that set divided by the number of genres it has.
A candidate with novelty 0 is not a side quest and is dropped, however strong its edge.

**The order is strength x novelty x centrality.** Strength is `log1p` of the reaching edge's
Layer 1 score, taken at the weakest link of the path and decayed once per extra hop. Novelty
is the share above. Centrality divides by the square root of how many seeds reached the
candidate. All three have to be present: a blockbuster edge into more of the same sinks on
novelty, a thin edge into a strange genre sinks on strength, and a show every favorite points
at sinks on centrality. The log is not cosmetic. Raw Layer 1 scores span more than an order of
magnitude while novelty is a share bounded at 1, so a plain product let the strongest edge in
the pool win on strength alone, which is precisely how the row came to read as a
recommendation list.

The only new number is a multiplier on Layer 1's own score. No show is scored a second time
and no second engine runs over the catalog, which keeps this inside ADR-08's rule that Layer
2 re-ranks rather than re-scores.

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

There is one rating in the development database, so the row was measured against 4000
synthetic three-seed users per sampler: **uniform**, three shows drawn at random, and
**coherent**, three shows drawn from a single genre, which is the harder and more realistic
case because a real taste is not random.

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

Hand-checked rows on the real catalog, run through the shipped function against a copy of the
development database:

- Breaking Bad, Better Call Saul, The Sopranos (Crime, Drama) leads with **The Boys**
  (1.865, all new), then Westworld and The Mandalorian (0.652 each, all new), and only then
  The Blacklist, whose bigger edge is discounted for landing back in crime.
- Friends, The Office, Modern Family (Comedy) leads with **Bones** (0.633, Crime and Drama
  both new), then The Simpsons, Grey's Anatomy, Castle.
- Grey's Anatomy, House, Suits (Drama) leads with **Criminal Minds** (1.975 x 0.67), and
  reaches The Boys and Lucifer further down.
- Rick and Morty, The Simpsons, Family Guy gets only 4 picks, because that taste already
  covers five genres including the two biggest, so little of the catalog is new to it. The
  row is short rather than padded.

### Alternatives, and why they lost
**A. Keep a cold-start row of the catalog's strongest cross-genre edges.** This is the
original decision, withdrawn above. It is one list for every visitor and surprising relative
to nothing.

**B. Require the pick to share NO genre at all with anything the user liked.** The strictest
reading of "a genre they have no positive history with", and the one that sounds right until
it is measured. At 100 shows it does not exist: 67 percent of coherent three-seed users get
**nothing at all**, the median row is 0 picks, and the few picks that do survive are junk
edges (median Layer 1 score 0.08, four fifths of them below the store's bottom quartile).
Drama is the reason, on 66 of 100 shows: like almost anything, and two thirds of the catalog
is disqualified from being fully novel. Rejected on the data, and named here rather than
quietly loosened, the same way the strict single-hop reading died at 63/100.

**C. Order by novelty first, edge strength second.** Fills exactly as well as the shipped
rule, because the gate and not the sort decides who is in the pool, but the top of the row
falls apart: median edge score of the first six picks drops to 0.29 against 0.88 under
strength x distance, and 41 percent of them sit below the store's bottom quartile. It
recreates, in a new place, the failure alternative E already measured.

**D. Walk the whole stored list instead of its strong half.** Median row grows from 6 to 11,
which is tempting, and the extra cards are the reason not to: positions 10 to 12 have a
median edge score of 0.195, with 68 percent below the store's bottom quartile. That is a
longer row of weaker reasons, and a side quest whose only claim is "one bit-part actor was in
both" cannot be explained to the person looking at it.

**E. A hard minimum edge score instead of a rank cap.** A floor at the store's median (0.439)
leaves 31 percent of unlocked users with no row at all; even a floor at its bottom quartile
costs 10 percent. A rank cap does the same job relative to each seed's own list, and it does
not hardcode a number that will drift the next time the catalog grows.

**F. Score the whole catalog a second time per user and take the middle band.** Rejected on
principle before it was measured, because it breaks ADR-08 twice over: it is a second engine
scoring every show from scratch, and at cold start it collapses to the shared quality prior,
which is one fixed ordering of the whole catalog shown to every visitor.

**G. Random unwatched shows.** Ruled out by intent, and the data agrees: a row of no-reason
cards cannot be explained, which is the thing ADR-08 says the product is.

**H. The long tail, meaning the lowest-popularity shows.** A popularity chart read from the
bottom. ADR-05 forbids ranking by popularity in either direction.

**I. The weak tail of every show's own Layer 1 list, ranks 8 and below.** The intuition was
that "fringe" means "weak edge". Those 313 edges are only 16 percent cross-genre and their
median score is 0.255 against 1.154 for the top three ranks: the tail is mostly more of the
same genre, held together by less evidence. Weakness is the wrong axis, and this finding is
now built in as the rank cap.

### Tags are not available, and would sharpen this
Patricio described the surprise as running on "connections and tags". The connections are
Layer 1 and are used. The tags are not: `Tag` and `ShowTag` are both empty, 0 rows, so genre
is the only categorical signal the catalog has today. Genre is coarse for this job, as the
Drama-on-66-shows number shows. Once tags are ingested, the same shape works with a finer
vocabulary: the novelty share would be computed over tags as well as genres, which would let
the row tell "a workplace comedy you have not tried" apart from "a comedy", and would push
the fully-novel band above 8 percent of picks. That is a later decision, not this one.

### One hazard this touched
`Show.Meta.ordering = ["-popularity"]` means any queryset that forgets an explicit
`order_by` silently becomes the popularity ranking ADR-05 forbids. Every queryset on the Side
Quests path is explicitly ordered and says so in a comment. Whether that default should
change repo-wide is a separate decision with a wider blast radius and is not settled here.

## After Action Review
Pending. Only Patricio can say whether the row does what he asked for, which is that a pick
feels like something you were unsure of but wanted to try. Fill this in after using it against
the local demo. Four things worth watching:

- The row is usually 5 to 7 cards, not 12. Whether a short row reads as honest or as broken
  is a taste question the numbers cannot answer.
- A pick's novelty is invisible on the card today. The objects carry `quest_new_genres`, the
  genres this user has never rated highly, so the row can say "for the Western in it" when
  the callout work (#4, #7) lands.
- Anonymous visitors currently see no Side Quests section and no copy at all, mirroring Top
  Picks. If the signed-out home page feels empty, the alternative is a sign-up-shaped prompt,
  which needs Patricio's words rather than an engineer's.
- The gate is 3 seeds. If the row often feels thin at exactly 3, the fix is more seeds rather
  than a looser surprise rule.

Tested in `shows/tests.py::SideQuestsTests`, which freezes the definition: the row is locked
below three high ratings, anonymous visitors get neither row nor copy, every pick lands in a
genre the user has never rated highly, the graph's strongest edge is refused when it is more
of the same, distance can beat a stronger edge and cannot win on its own, only the strong half
of a seed's list is walked, only the user's own favorites are walked, and an unlocked user
with nothing new gets no section rather than the locked copy.

`SideQuestsRankingTests` freezes the second amendment: a blockbuster edge no longer outranks a
novel one while strength still separates two equally novel shows, a show two hops out can be a
pick and loses to an identical show one hop in, a watched show is never a pick and still
carries the walk, a show every seed reaches ranks below one that only a single seed found, and
the surprise arithmetic is written out once against a known pick.
