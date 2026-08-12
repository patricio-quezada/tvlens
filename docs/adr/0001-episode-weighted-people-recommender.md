# 1. Recommend by episode-weighted shared people

Status: Accepted
Date: 2026-08-08

## Context
TVLens recommends shows by the people they share, cast and crew. Early versions
counted shared people equally, so a one-episode guest counted the same as a series
lead. After ingesting full episode credits from TMDb `aggregate_credits`, 84% of
cast rows were single-episode, and equal counting ranked shows by how many guest
actors two shows churned through: Grey's Anatomy returned NCIS on 715 shared guest
actors, which means nothing. Counting is not enough. How much of a show a person
actually made has to matter.

## Decision
Score candidate show B against source show A as:

    score(A, B) = sum over shared people of
        min( count_on_A / A.number_of_episodes,
             count_on_B / B.number_of_episodes )

- **Relative share** (`episode_count / number_of_episodes`) so cast and crew are the
  same unit, and a limited-series lead (6 of 6) equals a long-run lead (62 of 62).
- **`min()` takes the weaker end.** A Breaking Bad lead who did three episodes of
  Better Call Saul makes a weak edge, not a strong one.
- **Ratios cap at 1.0.** Credit rollups can exceed the episode total on returning
  series (595 credited episodes of a 594-episode run); nobody made more than all of a show.
- **One merged list, cast and crew together, no coefficient.** Episode share is the
  common currency, so the two edges merge without a cast-versus-crew exchange rate.
- **`SERVICE_JOBS` (casting roles) excluded on both sides.** A casting office works a
  studio's whole slate, so it links productions, not shows. Considered weighting them
  down instead; excluding is simpler and the signal is near-zero anyway.
- **Dedupe by person** at their best episode_count per show.
- **Sort by score, display the shared-people count** (the page stays legible while the
  math does the ranking).

Alternatives rejected: a hard episode threshold (cliff-edged, deletes thin shows,
hurts short-form); absolute counts (punishes limited series); separate cast and crew
lists (no single answer to "most similar").

## Consequences
- Better Call Saul tops Breaking Bad at score 14.79; CSI falls from 2nd (45 shared
  people) to 8th (0.19). The weighting does what counting could not.
- Computed in Python after fetching rows: the source-side ratio does not inject
  cleanly into a single ORM annotation. A materialised edge table is the eventual
  scale answer, not v1 (see the open scale-hardening issue).
- Coverage: 96 of 100 shows have at least one neighbour.
- Frozen by tests in `shows/tests.py`.
- Extended by the no-signal fallback ladder (weighted, then candidate-side estimate,
  then TMDb rating) for shows with no episode data yet; that will get its own ADR.
