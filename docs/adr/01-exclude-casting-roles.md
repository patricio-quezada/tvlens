---
adr: 1
title: Exclude casting roles from crew matching
status: accepted
date: 2026-08-11
tags:
  - adr
relates:
  - "[[04-episode-weighted-people-recommender]]"
---
# 1. Exclude casting roles from crew matching

**A casting office works across a studio's whole slate, so two shows sharing a casting director
says nothing about whether they are alike. Those roles are removed before crew is compared,
rather than down-weighted.**

## Context
The Boys and Grey's Anatomy came back as similar shows.

They share four crew members. All four are casting directors, all four from the same office: a
superhero satire tied to a medical drama by nothing more than the people who hired their
actors.

That is what sent me into the data model, and the pattern was general. A casting office works
across many of a studio's shows, so a shared casting director says nothing about whether two
shows resemble each other. Casting turned out to be the second most common source of these
false matches, behind only executive producers.

## Decision
Remove casting roles from both shows before comparing their crew, so a shared casting director
never counts toward two shows being similar.

The ingest keeps an explicit list of casting job titles to exclude rather than matching on the
word "casting," so the list is easy to check by hand. TMDb occasionally adds a new title that
slips the list -- a later review caught eight, including "Extras Casting" and "Location
Casting," which the list now carries.

I considered lowering casting's weight instead of removing it. It does not work. A casting
director credited on 60 episodes still says nothing about the show, so the episode weighting in
[ADR-04](04-episode-weighted-people-recommender.md) cannot fix this one. It is a separate
problem and it needs a separate answer.

## After Action Review
A show's similar list is now built from the people who actually made both shows, not from the
back-office staff a studio reuses across unrelated titles. The obviously wrong matches
disappeared and genuine ones took their place -- shows that really do share a creator, a
director, and a composer.

The cost is that a couple of shows whose only link was a shared casting office now show no
match at all. That is the honest answer, and a blank is better than a wrong one.

The same exclusion carries into the full weighted recommender,
[ADR-04](04-episode-weighted-people-recommender.md).
