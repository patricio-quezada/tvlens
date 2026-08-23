# 6. Lift the SQL variable ceiling in the recommenders

## Context
All three Layer 1 recommenders start the same way: gather the people on the source show,
then find the other shows those people also appear on. Until now each did that by pulling
the source show's people into a Python list and passing it to the database as
`person_id__in=[...]`, which binds one SQL variable per person.

SQLite caps how many variables a single query may bind. A modern bundled SQLite allows
32,766; an older system build allows only 999. The 100-show catalog stays well under
either, so nothing broke in testing. But the ceiling is real and close. One show in the
catalog, Scene of the Crime, already qualifies around 8,000 people, and a fuller import of
a decades-long soap crosses 32,766. When that happens the query does not slow down, it
throws `OperationalError: too many SQL variables`, and a public page that anyone can open
returns a 500. On an older SQLite with the 999 cap, 96 of the current 100 shows already
cross it.

This is not a bug in the ranking. The scores are correct. It is the gap between
works-on-my-catalog and safe-for-anyone-to-open.

## Decision
Stop binding one variable per person. How, depends on whether the code actually needs the
list of people in Python or only needs it to find matching shows.

**Cast and crew: push the id set into the database as a subquery.** `similar_by_cast` and
`similar_by_crew` never look at the person ids themselves. They only use them to join to
other shows and count the overlap. So the ids never need to leave the database. Instead of
fetching them into a Python list and sending them back as thousands of variables, the
query now nests the lookup:

    Show.objects.filter(
        cast__person_id__in=CastMember.objects.filter(show=show).values("person_id")
    )

The inner query is a *subquery*: a query used as a value inside another query. The database
runs the whole thing in one pass and binds no per-person variables at all, so the ceiling
simply does not apply. The crew version keeps its service-jobs exclusion inside the
subquery, so casting rows never seed the source set. A bonus: the intermediate list and its
extra round trip to the database are gone, which is leaner than before. An empty subquery
returns no rows, so a show with no cast (or no qualifying crew) still yields an empty
result, exactly as the old early return did.

**People: keep the list, but feed it in safe-sized batches.** `similar_by_people` is different.
It weights every shared person by episode share, so it needs each person's episode count in
Python to compute the score. A subquery cannot hand those counts back for per-person
arithmetic, so the code has to materialize the list. Here the fix is to *chunk* it: split the
person ids into batches of `SQLITE_MAX_VARS_SAFE` (900, comfortably under the oldest 999 cap)
and run the candidate queries once per batch, folding each batch's rows into the same running
totals. Because the fold takes each person's best episode count, the accumulated result across
batches is identical to one unchunked query. The scoring, the fallback ladder ([ADR-05](05-no-
signal-fallback-ladder.md)), and every recommendation result are untouched. Only the number of
variables per query changed.

I considered a third option from the issue: pin a modern SQLite version and document the
ceiling. I rejected it as a non-fix. It moves the wall from 999 to 32,766 without removing
it, it makes the app depend on the exact SQLite the host ships, and the soap-opera case
crosses even the higher cap. Removing the ceiling is cheaper to reason about than
remembering where it moved to.

## After Action Review
The ceiling is gone where it can be and safely bounded where a list is unavoidable. A show
with tens of thousands of qualifying people no longer 500s a public page. Measured against
the real catalog, nothing else moved: cast coverage stays 95 of 100, crew 88, either 96,
and Breaking Bad's weighted preview is unchanged (Better Call Saul 14.79, The Blacklist
1.09, CSI 0.19 in eighth). A regression test builds two shows sharing more people than the
batch size, spanning several chunks, and confirms all three recommenders return the right
overlap with no `OperationalError`; a change that dropped the subquery or the chunking
would fail there instead of in production.

One deliberate limit remains. `similar_by_people` still materializes its person list by
necessity, so its memory grows with the source show's cast and crew. That is bounded per
show and fine at any realistic size, but it is the reason this ADR could not make the
people path as clean as the cast and crew paths.

Still open, and out of scope here: the same pages run all three recommenders on every
anonymous request with no caching (issue #1, problem 2). Per-show caching invalidated on
ingest is the follow-up, and it also softens the blast radius of anything this ADR missed.

Provenance: issue #1 (Recommender scale hardening), migrated from Linear QUE-12.
