# 4. Recommend by episode-weighted shared people

## Context
We wanted to start building the recommendation algorithm, the part of TVLens that,
given one show, suggests others like it. The simplest possible start was to treat each
person two shows share, an actor or a crew member, as one "connection," and rank shows
by how many connections they share.

Then we pulled the full per-episode credits from TMDb (its `aggregate_credits` data),
and that broke the simple version. 84% of the cast entries were for a single episode.
Counting every shared person the same meant two shows looked similar just because they
had cycled through many of the same one-episode guest actors, which tells you nothing
about whether the shows are actually alike.

After we ran that ingest, the results were not useful. Counting is not enough. How much
of a show a person actually made has to matter.

## Decision
Instead of counting each shared person as one, weight them by how much of each show they
were really in, then add those weights up. That total is how similar two shows are.

"How much of a show a person was in" is their number of episodes on that show divided by
the show's total episodes. A lead who appears in all 62 episodes scores 1.0 for that
show; a guest in 1 of 62 scores about 0.02. Using this share instead of a raw episode
count means the lead of a 6-episode mini-series and the lead of a 62-episode drama both
score 1.0, so a short show is not penalized just for being short.

A shared person has a share on each of the two shows, so for that person we take the
smaller of the two. If someone starred in one show but only turned up in three episodes
of another, the link between those shows should count for the weaker side, not the
stronger one.

The smaller rules:

- **Cast and crew go in one ranked list.** Because everyone is measured as a share of a
  show, an actor and a director are already in the same unit, so the two can be ranked
  together with no separate scoring.
- **Each person's share is capped at 1.0.** Some credit data lists a few more episodes
  than a show actually aired (say 595 on a 594-episode run), and no one is in more than
  all of a show.
- **Casting directors are skipped.** A casting director is hired by a studio and works on
  many unrelated shows at once, so two shows sharing one tells you nothing about whether
  they are alike. We remove those roles before scoring.
- **A person credited more than once on a show counts once,** at their highest episode
  count.
- **We sort by the score but show the plain count of shared people** on the page, so it
  stays readable while the math does the ranking.

In code, for a source show A and a candidate show B:

    score = sum, over each shared person, of min(share_on_A, share_on_B)
    share = episode_count / number_of_episodes

We considered and passed on a few other approaches:

- A hard cutoff (only count people above some episode count) creates a cliff, throws away
  thin shows, and hurts short-form content.
- Counting raw episodes instead of a share punishes shorter shows.
- Keeping cast and crew as two separate lists leaves no single answer to "what is most
  similar."

## After Action Review
_(Named after the Army's after action review, a habit I am carrying over: did we get the
result we wanted, and if not, what do we change.)_

**What we wanted:** rankings that reflect how alike two shows really are, not how many
faces they happen to share.

**What happened:** it worked. Weighting moved the results in the right direction. For a
show like Breaking Bad, the top match becomes Better Call Saul, while a show that only
looked similar because the two share a long list of mostly-minor people drops far down.
96 of 100 shows come back with at least one match, and the rules are locked in by tests
so a later change cannot quietly undo them.

**What we would improve:** the score is worked out in code after pulling the rows from
the database, because the per-show share does not fit neatly into a single database
query. That is fine at 100 shows; at real scale the better answer is a table of
connections computed ahead of time, which is logged as an open item. The weighting also
does nothing for a show that has no episodes recorded yet; a separate decision ([ADR-05](05-no-signal-fallback-ladder.md))
covers that.
