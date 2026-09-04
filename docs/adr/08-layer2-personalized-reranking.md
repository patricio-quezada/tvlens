---
adr: 8
title: "Layer 2: personalize by re-ranking the graph per user"
status: accepted
date: 2026-08-18
tags:
  - adr
relates:
  - "[[05-no-signal-fallback-ladder]]"
  - "[[07-materialized-recommendations]]"
---
# 8. Layer 2: personalize by re-ranking the graph per user

**Two users looking at the same show saw the identical list. Layer 2 keeps a signed weight per
genre and tag from a user's own ratings and re-orders Layer 1's existing list with it: never
recomputing the graph, never scoring people twice, and never smuggling popularity in through
the cold start.**

## Context
Layer 1 is done. The shared-people graph is global, materialized
([ADR-07](07-materialized-recommendations.md)), and identical for everyone. It answers "shows
like this" from the data and deliberately ignores popularity
([ADR-05](05-no-signal-fallback-ladder.md)).

What it cannot do is tell two people apart. Open the same show as anyone else and the list is
the same list.

The next arc is personalization
([#6](https://github.com/patricio-quezada/tvlens/issues/6) for learned weights,
[#7](https://github.com/patricio-quezada/tvlens/issues/7) for personalized context), and it has
to arrive without costing the two things that make this recommender what it is: the
anti-popularity stance, and the fact that every recommendation can say *why*. There is a hard
constraint under it too. A brand-new user has rated nothing, so whatever this is has to work
from zero and get better as ratings arrive.

## Decision
Layer 2 is a per-user preference profile that **re-ranks** Layer 1's output.

**The signal is explicit ratings plus watched, and a rating implies watched.** A user rates a
show 0.5 to 5.0. Submitting a rating also marks it watched, because nobody rates what they have
not seen, and that holds at any score: a 1-star rating is still a watched show. Watched on
its own, with no rating, is a weaker unsigned positive. These are the only inputs. Layer 2
never infers taste from something a user did not deliberately do.

**It re-ranks the existing list rather than running a second engine.** Layer 1 stays exactly as
it is. Layer 2 takes its list and re-orders it, instead of sending the request through a
separate engine that scores every show from scratch. It nudges the order of something already
computed and stored, and never recomputes the graph per user. That mirrors ADR-07's split: the
store holds what is the same for everyone, the request computes what depends on the viewer.

**The profile is signed weights over genres and tags.** For each user the profile keeps an
affinity number per genre and per tag. Rating a show moves that show's genres and tags by a
*signed* amount: a high rating pushes them up, a low rating pushes them down. A 1-star rating
is information rather than noise (it says "less of this"), so it lowers those weights rather
than being discarded. A candidate's Layer-2 score is how well its genres and tags line up with
the user's weights.

**Layer 1 owns people; Layer 2 owns genres and tags.** Cast and crew already drive Layer 1's
similarity. If Layer 2 also weighted individual people, the same signal would count twice and
the two layers would fight. So the division is clean. A deliberate "favors this creator" boost
may arrive later, but as an explicit decision, never as an accidental second count of the
people Layer 1 already uses.

**Cold start encodes quality, never popularity.** A new user has no ratings, so their profile
starts from what correlates with high TMDb *quality* across the catalog: a typical-viewer
prior, not a chart. This is where popularity would creep back in if it were going to, and the
whole thesis of the recommender is that it does not. Until a user rates anything they mostly
see Layer 1's honest graph under a light neutral prior, and the profile personalizes away from
that base as ratings arrive.

**Interpretable weights, not embeddings, on purpose.** Because Layer 2 weights real, nameable
features, it can always explain itself: "ranked up because you rate shows with this creator or
genre highly." That explanation *is* the product, the context box in issue #7. A learned
embedding (a black-box numeric fingerprint of a show) might rank marginally better and
could not say why, which would kill the one thing that makes TVLens TVLens. Interpretability is
a requirement here, not a nicety.

## After Action Review
The hard part of a recommender is the design, not the code, and it is worth settling before
writing anything.

Two things stuck. Keep the layers honest, so Layer 1 stays global and Layer 2 only re-ranks and
nothing already working gets rebuilt. And keep it explainable, because the reason a show is
recommended is the product itself, which is why nameable weights beat a smarter black box
that could not account for itself.
