---
adr: 6
title: Lift the SQL variable ceiling in the recommenders
status: accepted
date: 2026-08-16
tags:
  - adr
relates:
  - "[[05-no-signal-fallback-ladder]]"
  - "[[07-materialized-recommendations]]"
---
# 6. Lift the SQL variable ceiling in the recommenders

**Every recommender passed the source show's people to SQLite as one bound variable each, which
throws a 500 past SQLite's limit. Cast and crew now push the id set down as a subquery and bind
nothing; the people path, which needs the ids in Python, runs in batches of 900.**

## Context
One show in the catalog, Scene of the Crime, already qualifies around 8,000 people. A fuller
import of a decades-long soap crosses 32,766.

That number matters because SQLite caps how many variables a single query may bind. A modern
bundled SQLite allows 32,766. An older system build allows 999. All three Layer 1 recommenders
pulled the source show's people into a Python list and handed it over as
`person_id__in=[...]`, which binds one variable per person.

Cross the cap and the query does not slow down. It throws `OperationalError: too many SQL
variables`, and a public page anyone can open returns a 500. On an older SQLite with the 999
cap, 96 of the current 100 shows already cross it.

Nothing broke in testing, because the 100-show catalog sits under either ceiling. This is not a
bug in the ranking. The scores are correct. It is the gap between works-on-my-catalog and
safe-for-anyone-to-open.

## Decision
Stop binding one variable per person. How depends on whether the code actually needs the people
in Python, or only needs them to find matching shows.

**Cast and crew: push the id set into the database.** `similar_by_cast` and `similar_by_crew`
never look at the person ids themselves. They use them to join to other shows and count the
overlap, so the ids never need to leave the database:

    Show.objects.filter(
        cast__person_id__in=CastMember.objects.filter(show=show).values("person_id")
    )

The inner query is a subquery, a query used as a value inside another query. The database runs
the whole thing in one pass and binds no per-person variables, so the ceiling stops applying
rather than moving. The crew version keeps its service-jobs exclusion inside the subquery, so
casting rows never seed the source set. The intermediate list and its extra round trip are gone
too, which is leaner than before, and an empty subquery returns no rows, so a show with no cast
still yields an empty result exactly as the old early return did.

**People: keep the list, feed it in batches.** `similar_by_people` is different. It weights
every shared person by episode share, so it needs each person's episode count in Python to
compute the score, and a subquery cannot hand those counts back for per-person arithmetic. The
list has to exist.

So the code chunks it: split the person ids into batches of `SQLITE_MAX_VARS_SAFE`, which is
900 and comfortably under the oldest cap, run the candidate queries once per batch, and fold
each batch into the same running totals. Because the fold keeps each person's best episode
count, the accumulated result across batches is identical to one unchunked query. The scoring,
the fallback ladder ([ADR-05](05-no-signal-fallback-ladder.md)) and every recommendation are
untouched. Only the number of variables per query changed.

The issue offered a third option: pin a modern SQLite and document the ceiling. That is not a
fix. It moves the wall from 999 to 32,766 without removing it, it makes the app depend on
whichever SQLite the host ships, and the soap-opera case crosses even the higher cap. Removing
a ceiling is cheaper to reason about than remembering where it moved to.

## After Action Review
The ceiling is gone where it can be and safely bounded where a list is unavoidable. A show with
tens of thousands of qualifying people no longer 500s a public page.

Nothing else moved, measured against the real catalog: cast coverage stays 95 of 100, crew 88,
either 96, and Breaking Bad's weighted preview is unchanged. A regression test builds two shows
sharing more people than the batch size, spanning several chunks, and confirms all three
recommenders return the right overlap with no `OperationalError`, so a change that dropped
the subquery or the chunking fails there rather than in production.

One deliberate limit remains. `similar_by_people` still materializes its person list out of
necessity, so its memory grows with the source show's cast and crew. That is bounded per show
and fine at any realistic size, and it is the reason the people path could not come out as
clean as the other two.

Still open and out of scope here: these pages run all three recommenders on every anonymous
request with no caching. [ADR-07](07-materialized-recommendations.md) is the follow-up.

Provenance: issue #1, Recommender scale hardening, migrated from Linear QUE-12.
