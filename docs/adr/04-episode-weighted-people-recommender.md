---
adr: 4
title: Recommend by episode-weighted shared people
status: accepted
date: 2026-08-11
tags:
  - adr
relates:
  - "[[01-exclude-casting-roles]]"
  - "[[02-aggregate-credits-ingest]]"
  - "[[05-no-signal-fallback-ladder]]"
  - "[[07-materialized-recommendations]]"
---
# 4. Recommend by episode-weighted shared people

**Counting shared people equally made two shows look alike when they had cycled through the
same one-episode guest actors. Each person is now weighted by the share of a show they actually
made, and a shared person counts for the weaker of their two sides.**

## Context
The first version of the recommender counted. Two shows share an actor, that is one connection;
share a director, that is two. Rank by the count. It was the simplest thing that could work,
and for a while it did.

Then the ingest pulled TMDb's full per-episode credits
([ADR-02](02-aggregate-credits-ingest.md)) and 84% of the cast entries turned out to be for a
single episode.

Counting every shared person the same now meant two shows looked similar because they had
cycled through the same pool of one-episode guests. That says nothing about whether the shows
are alike. The results stopped being useful the day the data got better, which is a strange way
to fail and a clear signal: counting is not enough. How much of a show a person actually made
has to matter.

## Decision
Weight each shared person by how much of each show they were really in, then add the weights
up. That total is the similarity between two shows.

A person's share of a show is their episode count divided by the show's total episodes. A lead
in all 62 episodes scores 1.0. A guest in 1 of 62 scores about 0.02. Using a share rather than
a raw count means the lead of a 6-episode mini-series and the lead of a 62-episode drama both
score 1.0, so a short show is not punished for being short.

A shared person has a share on each of the two shows, and the score takes the smaller one. If
someone starred in one show and turned up in three episodes of another, that link should count
for the weaker side.

    score = sum, over each shared person, of min(share_on_A, share_on_B)
    share = episode_count / number_of_episodes

The smaller rules:

- **Cast and crew go in one ranked list.** Because the weighting measures everyone as a share of
  a show, an actor and a director already share a unit, and one list ranks both with no separate
  scoring.
- **The weighting caps each person's share at 1.0.** Some credit data lists more episodes than a
  show aired -- 595 on a 594-episode run -- and no one is in more than all of a show.
- **The ingest skips casting directors.** A studio hires a casting director, who then works on
  many unrelated shows at once, so two shows sharing one says nothing about whether they are
  alike ([ADR-01](01-exclude-casting-roles.md)).
- **A person credited more than once on a show counts once,** at their highest episode count.
- **The ranking sorts by score and shows the plain count of shared people** on the page, so it
  stays readable while the arithmetic does the ranking.

Three approaches lost:

- A hard cutoff, counting only people above some episode count, creates a cliff, throws away
  thin shows, and hurts short-form content.
- Counting raw episodes rather than a share punishes shorter shows.
- Keeping cast and crew as two separate lists leaves no single answer to "what is most similar."

## After Action Review
It worked. For Breaking Bad the top match becomes Better Call Saul, and the show that only
looked similar because the two share a long list of mostly-minor people drops far down. 96 of
100 shows come back with at least one match, and tests lock the rules in so a later change
cannot quietly undo them.

Two things I would improve. The code works the score out after pulling the rows from the
database, because the per-show share does not fit neatly into a single query. That is fine at
100 shows and does not scale; the answer is a table of connections computed ahead of time,
which [ADR-07](07-materialized-recommendations.md) later builds.

And the weighting does nothing at all for a show with no episodes recorded yet. Every score
comes out zero, which needs its own answer:
[ADR-05](05-no-signal-fallback-ladder.md).

## Amendment, 2026-08-26: the weight is superlinear
**status: draft**

The weight above is linear, and that is not enough. A person in 3 of 100 episodes contributes
0.03, which is correct as a description of their involvement but wrong as a vote. The problem
is arithmetic: 217,622 of the catalog's 278,632 cast rows carry `episode_count = 1`, and the
"no floor" promise means every one of them counts. Three hundred one-episode guests sum to 3.0
and beat a genuine full-run co-lead at 1.0. Mass beats quality, catalog-wide, and 463 pairs in
the stored top-12 lists were ordered that way.

The rule becomes:

```
score(A, B) = sum over shared people of
    min(share on A, share on B) ** INVOLVEMENT_EXPONENT
```

with `INVOLVEMENT_EXPONENT = 1.375`. The same exponent applies to the `estimate` accumulator
feeding [ADR-05](05-no-signal-fallback-ladder.md)'s middle rung, because that rung already
claims to rank by how much of a show a person is, and weighting only the top rung would leave
the two disagreeing about what involvement means. It also applies to `shared_connections`,
which recomputes the same arithmetic for the callout.

**This is not the hard cutoff rejected above.** That was rejected for creating a cliff, throwing
away thin shows, and hurting short-form content. A continuous reweighting does none of those:
every shared person still contributes, and no edge is thrown away. Measured across all 37,950
candidate edges in the catalog, not one reaches exactly 0.0, and the smallest is 9.6e-06, so
ADR-05's requirement that the candidate set never change is untouched.

The shape follows `SIDE_QUEST_CENTRALITY_EXPONENT` in `personalization.py`, whose comment makes
the same argument in the same form: being reached twice should cost something, but it should
not disqualify.

### Why 1.375, which is the part that needs the numbers

1.375 reads as a compromise where 0.5 reads as a principle, so the defence has to be the
measurement rather than the shape of the number.

**The acceptance case.** Three hundred guests at one episode of a hundred, against one co-lead
who carried the whole run of both shows. Break-even is `ln(300) / ln(100) = 1.24`.

| exponent | the crowd totals | the co-lead wins by |
|---|---|---|
| 1.0 | 3.0000 | loses |
| 1.25 | 0.9487 | 1.05x |
| **1.375** | **0.5335** | **1.87x** |
| 1.5 | 0.3000 | 3.33x |

1.25 clears the case by five percent, which puts the decision on a coin edge. 1.375 clears it
with room. Against the real catalog, 1.25 fixes 75% of the 441 remaining mass-beats-quality
pairs, 1.375 fixes 83%, and 1.5 fixes 90%.

**Why not higher, which is the finding that decided it.** Going up looks strictly safer and is
not. Past roughly 1.4 the score collapses toward whoever holds the single strongest tie, and the
mid-strength tail stops carrying weight. That tail is what encodes register and production
milieu: a shared recurring cast, a shared director pool. Of the 16 sources whose top pick
differs between 1.375 and 1.5, about ten are better at 1.375 and one is better at 1.5.

| source | at 1.375 | at 1.5 |
|---|---|---|
| The Pitt | ER | Lanterns |
| Pokémon | Pokémon Horizons | Detective Conan |
| Silent Witness | Midsomer Murders | Loki |
| Newsnight | Sunday with Laura Kuenssberg | Black Mirror |
| Horizon | NOVA | Sunday with Laura Kuenssberg |
| The Voice | The Kelly Clarkson Show | Gran hermano |

1.375 is the top of that plateau, not a midpoint between two guesses.

### What it moved

On the 464-show catalog, together with the widened `SERVICE_JOBS`
([ADR-01](01-exclude-casting-roles.md), amended the same day): 4,014 edges become 4,013 across
the same 409 sources, 304 of those sources reorder, and 76 change their top pick. The
distribution goes from min 0.0001, max 33.376, mean 0.8388, median 0.4572 to min 0.0000039,
max 28.278, mean 0.4612, median 0.1351. The mean barely moves and the median collapses, which
is the intended shape: blowout edges survive, the indifferent tail flattens.

Breaking Bad, which ADR-07's review recorded, now reads: Better Call Saul 9.7473, The Blacklist
1.0204, Malcolm in the Middle 1.0133, Westworld 0.3775, The Mandalorian 0.3641, The Boys 0.3584,
House of the Dragon 0.3552, Fargo 0.3418.

**One consequence worth stating plainly.** The Blacklist beats Malcolm in the Middle by 0.0071.
Both rest on a single person at a full 1.0: Dave Porter, who scored every episode of both, and
Bryan Cranston, who led both. They are separated only by tail size, so a composer ties a lead.
That is the direct cost of keeping composition out of the widened `SERVICE_JOBS`, it was seen
and accepted, and `check_ground_truth` prints the margin on every run rather than asserting on
it, because it is too small to assert on.

### Downstream

`RANK_STEP` in `personalization.py` needs no change, and the reasoning is worth recording
because the obvious move is wrong. Preserving the mean would set it to 0.573. But the mean is
dragged by blowouts, and the constant's own comment asks that "a blowout edge resists
personalization while the tail reorders freely to taste". The median score falls from 0.44 to
0.14 while Better Call Saul still contributes 9.75, so the exponent serves that intent better
than the linear weight did. Re-fitting `RANK_STEP` down would undo the improvement.

`SIDE_QUEST_HOP_DECAY` is tilted but deliberately left alone; see
[ADR-09](09-side-quests-cross-genre-edges.md).

### Correction to the record

The first survey of which credits the exponent would promote classified "below the line" as
"absent from `MARQUEE_JOBS`". That was wrong. `MARQUEE_JOBS` decides who gets named in prose and
was never a signal list, so the survey wrongly flagged `Characters`, `Story`, `Comic Book` and
`Original Series Creator`, which are source-material credits and among the strongest show links
in the catalog. It also produced a headline finding, "Producer, 19 pairs", that did not survive
re-derivation: bare `Producer` makes only four full-run pairs and every one of them is correct
(Star Trek: The Next Generation to Voyager and to the original series, Frieren to Hunter x
Hunter), while excluding it would have dropped the person entirely on 45% of its credits. The
corrected method, counting only pairs where the job is the person's sole credit on that show, is
what the ADR-01 amendment is built on.

### Tests

`SimilarByPeopleTests` fixture was rewritten, not just renumbered. It gave both shows 10
episodes and nearly every credit `episode_count=10`, so almost every share was exactly 1.0, and
`1.0 ** anything` is `1.0`: only two assertions in 248 moved when the exponent went from 1.0 to
1.375. A fixture that cannot see the thing it exists to freeze is worse than no fixture. Shares
now span 0.01 to 1.0.

`InvolvementExponentTests` adds the acceptance case, which nothing tested before. It asserts the
1.87x margin rather than only the ordering, so 1.25 fails it, and bounds the exponent inside the
measured plateau, so 1.5 fails it too.

`check_ground_truth` is a new management command holding the catalog-dependent facts. They are
not unit tests because the test database is empty and recreating Breaking Bad's 75 shared people
as a fixture would freeze a copy of the data rather than the data, which is the failure mode it
exists to prevent. See [ADR-07](07-materialized-recommendations.md).
