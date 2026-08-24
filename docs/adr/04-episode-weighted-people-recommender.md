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
