# 5. Exclude casting roles from crew matching

## Context
Recommending by shared crew connects shows through the people who made them. But a
**casting office works a studio's whole slate**, so counting casting credits links
productions that have nothing to do with each other. On the catalog, The Boys and
Grey's Anatomy came back sharing four crew members, and every one was a casting
director from the same office, a superhero satire tied to a medical melodrama by the
office that hired the actors. Casting produced the second most connections of any role,
behind only Executive Producer.

## Decision
Maintain a `SERVICE_JOBS` list of casting-type jobs and exclude them from **both
sides** of the crew comparison. Exact titles by choice (auditable), with the caveat
documented in code that a new TMDb import can mint a variant that slips the list;
the 2026-08-06 review found eight such variants leaking 45 rows ("Extras Casting",
"Location Casting", ...) and extended the list. Re-audit query lives in the comment.

Alternatives rejected: weighting casting down instead of excluding (a casting
director on 60 episodes still says nothing about the show, so `episode_count` does
not fix this, it is a different problem that stacks with the weighting in ADR-0001);
substring-matching "casting" (rejected in favour of an auditable explicit list).

## Consequences
- Excluding casting costs 2 shows of coverage and removes 13 false pairs, while
  promoting real ones: Westworld ↔ Person of Interest surfaces (J.J. Abrams, Jonathan
  Nolan, Ramin Djawadi), and the Law & Order / Chicago franchise links appear.
- `SERVICE_JOBS` survives into the weighted recommender (ADR-0001) unchanged.
