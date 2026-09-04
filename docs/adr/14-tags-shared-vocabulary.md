---
adr: 14
title: "Tags are a shared vocabulary applied privately"
status: accepted
date: 2026-08-24
tags:
  - adr
relates:
  - "[[08-layer2-personalized-reranking]]"
  - "[[12-catalog-search]]"
---
# 14. Tags are a shared vocabulary applied privately

**A `Tag` row is shared across every reader. The `ShowTag` row that applies it belongs to one
person. Two readers can agree that "slow burn" means something without either of them seeing the
other's shelf, and matching is on the slug so that "Slow Burn" and "slow burn" cannot become two
tags.**

## Context
Genres come from TMDb and describe what a show *is*. Nothing described what it was *like to watch*,
which is the judgement a viewer actually trades with other viewers.

The models had existed since the first schema and held zero rows. `personalization.py` was already
reading `ShowTag` on two code paths, weighting tag affinity by relevance, so the recommender had a
live input that could never contain anything.

Three shapes were possible.

**Fully private tags**, one vocabulary per reader, is the simplest and the least useful. Everyone
invents their own spelling of the same idea, no two readers' tags are comparable, and the
recommender learns from a vocabulary of one.

**Fully public tags**, where applying a tag changes what everyone sees, makes a show's tag list a
thing to argue about. It also makes one bad actor's vocabulary everyone's problem, and it is the
failure mode that turns tagging systems into moderation systems.

**Shared vocabulary, private application** is what MovieLens calls a genome. The word is common
property; who applied it to what is not.

## Decision
`Tag` holds the word. `ShowTag` holds one person applying one word to one show, with a relevance
weight, unique on `(user, show, tag)`.

**Matching is on the slug, not the name.** `slugify("Slow Burn")` and `slugify("slow burn")` are
both `slow-burn`, so the second one reuses the first tag rather than creating a rival. Letting both
exist would halve the signal `learned_tag` reads, and the split would be invisible in the UI.

**Suggestions come in three tiers**, and the order carries the whole idea. Tags other readers put
on *this show* rank first, ordered by how many readers chose each, because that is the closest
thing to a second opinion the catalog can offer. The vocabulary in general use comes next, by
frequency. Everything else is alphabetical.

**Only names cross the boundary.** The suggestion strip shows what a show has been called. It never
shows who called it that.

**Tagging saves in place**, following [ADR-10](10-rating-saves-in-place.md). The first version
redirected to `#tags` and threw the reader a third of the way down the page, which is the exact
behavior ADR-10 was written to eliminate.

Tags are searchable with `tag:` and ranked alongside genre and network in
[ADR-12](12-catalog-search.md), because a tag is the same kind of claim about a show.

## After Action Review
Written on the same day as catalog search, and the two fit together better than either was designed
to. Search gave tags somewhere to be useful the moment they existed.

**The cold start is worse than ratings.** A rating costs one click; a tag costs a decision about
what a show was like. Until several readers have tagged the same show, the suggestion strip is
empty and the first tier does nothing, which is the tier carrying the design.

`ShowTag.relevance` is written as `1.0` for every row and nothing sets it otherwise. The field
exists because `personalization.py` already weights by it. Whether a reader should ever set it, or
whether it should be derived from how many people applied the same tag, is undecided and deliberately
so.

The `Recommendation` model was deleted in the same commit. Nothing wrote it and nothing read it:
[ADR-07](07-materialized-recommendations.md) had materialized `SimilarShow` instead, leaving a table
whose `reason` field was never going to hold anything.
