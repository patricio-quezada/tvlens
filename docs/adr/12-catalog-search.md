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
season names and episode synopses, modelled on Obsidian's advanced search rather than a single
text box.

The obvious implementation is one `filter()` with every branch ORed together. On the 100-show
catalog, for a query with sixteen real hits, that query was killed at eight seconds twice and was
still executing after two minutes on a third run. It does not return a slow answer. It does not
return.

The cause is fan-out, not scan cost. Cast and crew are reverse foreign keys holding 128,577 and
24,482 rows behind 100 shows. Django compiles a single `filter()` containing both into one SELECT,
and SQLite materialises the cross product before `DISTINCT` collapses it: 61 million rows for cast
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

Search costs about 90 ms for free text across 464 shows and 486,829 episodes, and 11 to 15 ms for a
scoped operator query. The two heaviest branches are cast at 57 ms before the two-step rewrite, now
9 ms, and episode synopsis at 49 ms, which is an irreducible scan of 486,829 text fields.

**Cost scales with cast size, not show count.** The catalog grew 4.6x and the cast table grew 2.2x,
but free-text search only doubled. A catalog an order of magnitude larger should revisit FTS5.

The branch weights are a ranking decision with no evidence behind them yet. A title hit outranking a
fourth-billed actor is obviously right; whether an episode synopsis should outrank a season name is
a guess, and the first real feedback should overturn it.

Ranking never uses popularity, per ADR-05.
