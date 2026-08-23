# 1. Exclude casting roles from crew matching

## Context
Looking at the data model, I found the recommendation engine counting casting directors, the
people a studio hires to pick actors, as a reason two shows are similar. They should not be. A
casting office works across many of a studio's shows, so two shows can share the same casting
director without being anything alike.

For example, The Boys and Grey's Anatomy came back as similar because they shared four
crew members, and all four were casting directors from the same office: a superhero
satire tied to a medical drama by nothing more than the office that hired their actors.
Casting turned out to be the second most common source of these false matches, behind
only executive producers.

## Decision
Remove casting roles from both shows before comparing their crew, so a shared casting
director never counts toward two shows being similar.

The ingest keeps an explicit list of casting job titles to exclude rather than matching on the
word "casting," so the list is easy to check by hand. TMDb occasionally adds a new title that
slips the list (a later review caught eight, such as "Extras Casting" and "Location Casting,"
which the list now carries).

I considered lowering casting's weight instead of removing it, but a casting director
credited on 60 episodes still says nothing about the show, so the episode weighting in
[ADR-04](04-episode-weighted-people-recommender.md) does not fix this. It is a
separate problem.

## After Action Review
A show's similar-shows list is now built only from the people who actually made both
shows, not from the back-office staff a studio reuses across unrelated titles. The
obviously wrong matches disappear and the genuine ones take their place, shows that
really do share a creator, a director, and a composer. The only cost is that a couple of
shows whose single link was a shared casting office now show no match, which is the
honest answer.

The same exclusion carries into the full weighted recommender,
[ADR-04](04-episode-weighted-people-recommender.md).
