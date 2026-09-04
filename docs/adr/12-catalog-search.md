---
adr: 12
title: "Catalog search: one query per branch, matched on word boundaries"
status: accepted
date: 2026-08-24
tags:
  - adr
relates:
  - "[[05-no-signal-fallback-ladder]]"
  - "[[06-sql-variable-ceiling]]"
---
# 12. Catalog search: one query per branch, matched on word boundaries

**Search never ORs two fan-out relations into a single `filter()`, because that query does not
return. Every branch runs as its own query and the ids are unioned in Python. Every branch matches
on a word boundary, using a cheap `LIKE` to prefilter and a regex to decide, because a plain
substring match answers three-letter queries with garbage.**

## Context

Issue #8 asked for search over titles, cast, crew, characters, descriptions, genres, networks,
season names and episode synopses, modeled on Obsidian's advanced search rather than a single
text box.

The obvious implementation is one `filter()` with every branch ORed together. On the 100-show
catalog, for a query with sixteen real hits, that query was killed at eight seconds twice and was
still executing after two minutes on a third run. It does not return a slow answer. It does not
return.

The cause is fan-out, not scan cost. Cast and crew are reverse foreign keys holding 128,577 and
24,482 rows behind 100 shows. Django compiles a single `filter()` containing both into one SELECT,
and SQLite materializes the cross product before `DISTINCT` collapses it: 61 million rows for cast
crossed with crew, 368 million once genres and networks join in. No index changes that number,
because it is a property of the query shape.

The second problem only appeared once results were readable. `icontains` compiles to
`LIKE '%x%'`, an unanchored substring match, and substrings lie. `hbo` returned 46 shows through
the word "neighbour". `war` returned 883 through "toward" and "warm". A search that answers a
three-letter query with garbage is worse than one that answers nothing.

## Decision

**One query per branch, ids unioned in Python.** Nine branches, each touching one relation. The
same search that would not return runs in 32 ms this way, 74 ms worst case, with identical results.
`shows/search.py` holds them in `_branch`, and a test reads that function's source and fails if
`cast__` and `crew__` ever appear in the same `filter()` again. A timing assertion would pass on a
fast machine with a small fixture even after the bad shape came back; the shape is what must be
guarded.

**Word-boundary matching, prefiltered by LIKE.** Anchoring with `\b` alone fixes the results and
costs 12x, because Django implements REGEXP on SQLite as a Python callback and pays a function call
per row: 92 ms against 7 ms. ANDing the two predicates lets SQLite run the cheap `LIKE` first and
the regex only on the rows that survive it. Measured at 7 to 13 ms, the speed of the substring
match with the regex's answers. Word *prefixes* still match, so `break` finds Breaking Bad while
`hbo` no longer finds a neighbour.

**People are found in two steps, not one join.** Joining Show to cast to person makes SQLite
re-evaluate the name predicate once per credit row, and the catalog holds 278,632 cast rows against
154,699 people. Scanning people once and looking shows up by an indexed foreign key measured 59 ms
against 9 ms for the same 26 results, and 64 ms against 25 ms for a surname matching 2,646 people.
The id list is batched at 900 for the reason ADR-06 records.

**No text index.** `LIKE '%x%'` is unanchored, so SQLite full-scans whatever index sits on the
column; `db_index=True` would change nothing. FTS5 with the trigram tokenizer is available in this
build, confirmed by creating a table and matching against it, and would change the complexity. It
is not adopted, because there is no latency to recover: the slowest single scan in the catalog is
Person.name at 5.1 ms across 154,699 rows.

**A year and a season number typed into the box filter, with no operator and no button.** Both are
lifted out of the string before the text search runs. Leaving them in would run them twice, and
"Season 3" as free text matches nearly every show in the catalog. A year means *was airing then*,
not *premiered then*, and a show with no `last_air_date` has not ended.

**Operators scope, and stack by intersection.** `actor:`, `genre:`, `network:` and the rest narrow
to one branch. Someone typing two of them is narrowing, not widening, so they AND. An operator that
is typed but not understood is named on the page rather than silently treated as text: a filter the
reader cannot see is a filter they cannot undo.

## Consequences

**Re-measured 2026-08-30**, on 248 shows and 164,176 episodes after the catalog was filtered and
pruned. The original figure here was a single number for free text, which turned out to hide the
only thing that matters.

Scoped operator queries are **6 to 10 ms**. Free text is **48 to 235 ms**, and the spread is not
noise: it is one branch, and it tracks how common the word is rather than how big the catalog is.

| branch | "the" | "murder" | "zeppelin" |
|---|---|---|---|
| title | 0 ms | 0 ms | 0 ms |
| show overview | 1 | 0 | 0 |
| season name | 1 | 0 | 0 |
| **episode synopsis** | **176** | **35** | **18** |
| episode rows surviving LIKE | 76,882 | 4,024 | 4 |

Every branch except episode synopsis is free at any term. The synopsis branch pays a floor of
about 18 ms to scan, then roughly 2 microseconds for each row the `LIKE` lets through and the
word-boundary regex has to re-test. A common word defeats the prefilter, so the regex runs on
half the table.

**The waste is worst where the result is least useful.** Searching "the" costs 235 ms to return
the 120-show cap, which is not an answer to anything. A term matching half the corpus has no
power to discriminate, so the branch could be skipped on exactly the terms that make it slow.
That is a behavior change and belongs in a proposal, not here.

**Cost scales with term frequency, not catalog size.** The catalog since shrank by 46% and the
worst case got worse, because the pruned shows were low-signal ones whose synopses matched
nothing. FTS5 remains the real answer whenever this stops being tolerable.

The branch weights are a ranking decision with no evidence behind them yet. A title hit outranking a
fourth-billed actor is obviously right; whether an episode synopsis should outrank a season name is
a guess, and the first real feedback should overturn it.

Ranking never uses popularity, per ADR-05.

## Amendment, 2026-09-01: the episode branch reads FTS5, for bm25 more than speed

The episode synopsis branch now queries an FTS5 index instead of scanning
164,360 overviews with LIKE plus a regex. Every other branch is unchanged, and
so is the "no text index" reasoning above: those branches still have no latency
to recover. This one had something better to recover, which is ranking.

Ranking is the reason, and #29 framed it that way before the build. This
record already admitted the branch weights are a guess; below the weights, the
old branch had no ordering at all, so a show whose episodes are about the
query sorted level with a show that mentions it once, and the crowd score
decided. bm25 now orders shows within the episode bucket by their best
episode's match. It reaches no further: weighing bm25 against the other
branches would overturn the branch ordering, and that needs its own evidence.

Matching is prefix, not exact, because exact would have been a silent
behavior change: `murder*` returned the regex's 151 shows where exact FTS
matching lost "murderer" and "murders" and returned 132. Measured on the live
catalog, prefix FTS returned exactly the regex's shows for every term tried.
The user's term never reaches MATCH raw; it is quoted into a single phrase
with internal quotes doubled, because FTS5 treats quotes, hyphens and AND, OR,
NOT as syntax, and `don't` must be a word rather than an error.

The numbers, re-measured 2026-09-01 on 249 shows and 164,360 episodes. The
branch fell from 170 ms to 81 ms on "the", 34 to 5, and 17 to 0.1 on "murder"
and "zeppelin". Half of the 81 is the bm25 aggregation itself: ids alone come
back in 41 ms, which matches what #29 predicted. The price is size, 43 MB on a
77 MB database, a little over the 37 MB #29 measured because every episode row
is mirrored, empty synopses included, to keep the sync invariant one-to-one.
The index built in under a second.

The index creates an obligation the old scan never had: it must answer for the
episodes table as it is now. Three SQLite triggers discharge it at write time,
on insert, update and delete, created in the same migration that builds the
table, so no ingest command has to remember anything. The update trigger fires
on `season_id` as well as `overview`, because TMDb moves episodes between
seasons while keeping their id, and an overview-only trigger would leave a
moved episode answering for its old show.

The table carries show_id, season_id and episode_id unindexed, so the season
pivot (#25, #26) becomes a change of SELECT column rather than an index
rebuild. Tested in `FtsQueryEscapingTests`, `EpisodeFtsSearchTests` and
`EpisodeFtsTriggerTests`. The Python-side early-exit fallback #29 kept in
reserve was not needed.
