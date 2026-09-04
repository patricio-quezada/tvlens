---
adr: 1
title: Exclude casting roles from crew matching
status: amended
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
slips the list; a review on 2026-08-06 caught eight, including "Extras Casting" and
"Location Casting," which the list now carries.

I considered lowering casting's weight instead of removing it. It does not work. A casting
director credited on 60 episodes still says nothing about the show, so the episode weighting in
[ADR-04](04-episode-weighted-people-recommender.md) cannot fix this one. It is a separate
problem and it needs a separate answer.

## After Action Review
A show's similar list is now built from the people who actually made both shows, not from the
back-office staff a studio reuses across unrelated titles. The obviously wrong matches
disappeared and genuine ones took their place: shows that really do share a creator, a
director, and a composer.

The cost is that a couple of shows whose only link was a shared casting office now show no
match at all. That is the honest answer, and a blank is better than a wrong one.

The same exclusion carries into the full weighted recommender,
[ADR-04](04-episode-weighted-people-recommender.md).

## Amendment, 2026-08-26: the argument covers more than casting
**status: accepted 2026-09-04**

The reasoning above is not really about casting. It is about a credit that ties two productions
together without saying anything about the two shows, and casting was simply the first and most
obvious case. The involvement exponent in
[ADR-04](04-episode-weighted-people-recommender.md), amended the same day, made the rest of that
class visible.

The exponent makes a single full-run credit decisive. That is what it is for, and it works: a
co-lead who carried both shows now outranks a crowd of guests. But it does not know a co-lead
from a sound mixer. Of the 525 show pairs in the catalog sharing someone at an episode share of
0.9 or above, 121 were held together by a facility or a vendor. Under the linear weight they
were invisible, because one person's 1.0 drowned in the crowd. Under the exponent they decide
rankings:

- Miami Vice recommended Real Time with Bill Maher, on one man credited as marine coordinator
  for all 110 episodes of the first and as a writer for all 740 of the second.
- Elementary recommended Marvel's Daredevil, on a colorist who graded the full run of both.
- The Apothecary Diaries reached SPY x FAMILY, Frieren and Isekai Office Worker through a sound
  director and a music producer. Anime sound houses work a studio slate exactly the way a
  casting office does.

So `SERVICE_JOBS` gains 85 titles in four families, each with one argument, all exact titles in
the style already decided above, and all present in the catalog today.

**Sound post, 45 titles.** A sound house or post facility is booked across a slate. This is the
original argument word for word, in a different department.

**Music service, 9 titles, never composition.** A music supervisor licenses tracks across a
slate and a music house produces for whoever books it. Composition is deliberately absent: a
score is authorial, `MARQUEE_JOBS` already treats it as show-defining, and its 34 full-run pairs
are mostly right, including Breaking Bad to Better Call Saul.

**Picture finishing, 15 titles.** The post house that finishes the picture, contracted per
production. This is the Elementary case.

**Vendors and on-set services, 16 titles.** Drivers, transport, catering, medics, security,
animal and marine coordinators. Hired by the production, not part of the show. This is the Miami
Vice case.

The cost is small because `SERVICE_JOBS` filters credit rows rather than people: a person is
only lost when the excluded job is their sole credit on that show. The widening removes 1,985 of
56,787 crew credits (3.5%) and 1,820 of 280,229 person-show links (0.6%). Cast credits are
untouched. No show loses its last person, and the 409 sources with at least one edge stay 409.

### What was considered and kept, which matters as much

| credit | why it stays |
|---|---|
| Executive Producer | 102 full-run pairs, the largest source of real authorial linkage in the catalog |
| Producer | only 4 full-run pairs and all correct: Star Trek TNG to Voyager and to the original, Frieren to Hunter x Hunter. Excluding it would drop the person entirely on 45% of its 2,084 credits |
| Co-Executive Producer | same tier; Vince Gerardis ties Game of Thrones to House of the Dragon |
| Original Music Composer | a score is authorial, and its pairs are mostly right |
| Main Title Theme Composer, Theme Song Performance | Ron Grainer ties Doctor Who to Doctor Who, Ken Freeman ties Casualty to Holby City |
| Characters, Story, Comic Book, Graphic Novel, Original Series Creator | source material, the strongest possible show link. Siegel and Shuster tie Smallville to Lois & Clark, Roddenberry ties Strange New Worlds to Voyager |
| Editor | 4 full-run pairs. Excluding 1,520 credits to fix four is a bad trade |
| Art Direction, Production Design, Costume Design, Director of Photography, Stunt Coordinator | department heads are genuine creative contributors, 7 pairs between them |
| Associate Producer, Line Producer | genuinely below the line, but zero full-run pairs, so excluding them buys nothing and widens the blast radius |
| Hair and Makeup Department Head | the weakest call. Locally hired, and their one pair (Lanterns to The Pitt) is wrong, but they are department heads, so the list stops short of the craft departments |

The bare `Producer` row is the one to re-read before widening this list again. The first survey
of offenders named it the largest problem, and that was an artifact of classifying "below the
line" as "absent from `MARQUEE_JOBS`", which is a naming list rather than a signal list.
Re-derived properly, counting only pairs where the job is the person's sole credit on that show,
`Producer` turned out to be carrying signal rather than noise. See ADR-04's correction note.

### The audit still applies

The 2026-08-06 instruction above still holds for casting, and the same instruction now applies
to these families: when touching ingest, look for new variants that slip the exact-title list.
The catalog has 616 distinct crew job titles today, so a new import can mint one. Four residual
top picks are still driven by a credit of this kind after the widening, through
`Costume Design`, `Property Master`, `Editor` and `Animation Director`, all marginal and all
deliberately left in.

`test_facility_job_excluded` and `test_composing_is_not_a_facility_job` freeze both sides of the
line this amendment draws.
