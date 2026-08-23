# 8. Layer 2: personalize by re-ranking the graph per user

## Context
[Layer 1](../../shows/recommenders.py), the shared-people graph, is done: global,
materialized ([ADR-07](07-materialized-recommendations.md)), and the same for every viewer.
It answers "shows like this" from the data, and it deliberately ignores popularity
([ADR-05](05-no-signal-fallback-ladder.md)). What it cannot do is personalize. Two users
looking at the same show see the identical list.

The next arc ([#6](https://github.com/patricio-quezada/tvlens/issues/6), dynamic/learned
weights; [#7](https://github.com/patricio-quezada/tvlens/issues/7), personalized context) is
personalization: making the list reflect what a given user actually likes. The design has to
add that without losing the two things that make this recommender what it is, its
anti-popularity stance and its interpretability, the fact that every recommendation can say
*why*, which is what powers the callout. It also has a hard constraint: a brand-new user has
rated nothing, so it must work from zero and improve as ratings arrive.

## Decision
Layer 2 is a per-user preference profile that **re-ranks** Layer 1's output.

**The signal is explicit ratings plus watched, and a rating implies watched.** A user rates a
show 0.5 to 5.0. Submitting a rating also marks that show watched, because nobody rates what
they have not seen; this holds no matter the score, a 1-star rating is still a watched show.
Watched on its own, with no rating, is a weaker, unsigned positive. These are the only inputs.
Layer 2 never infers taste from something the user did not deliberately do.

**Personalization re-ranks the existing list, it does not run a second engine.** Layer 1 stays
exactly as it is: the global, materialized list of candidate shows. Layer 2 takes that list and
re-orders everything in it to fit the user, rather than sending the request through a separate
engine that scores every show from scratch. Layer 2 nudges the order of a list that is already
computed and stored, and never recomputes the graph per user. This mirrors the split from
ADR-07: the store holds what is the same for everyone once, and the request computes what
depends on the viewer live over a handful of rows.

**The profile is signed weights over genres and tags.** For each user the profile keeps an
affinity number per genre and per tag. When a user rates a show, that show's genres and tags
move the user's weights by a *signed* amount: a high rating pushes them up, a low rating pushes
them down. A 1-star rating is information, not noise, it says "less of this", so it lowers
those weights rather than throwing them away. A candidate show's Layer-2 score is then how well
its genres and tags line up with the user's weights.

**Layer 1 owns people; Layer 2 owns genres and tags.** Cast and crew already drive Layer 1's
shared-people similarity. If Layer 2 also weighted individual people, the same signal would
count twice and the two layers would fight each other. So the division is clean: shared people
belong to Layer 1, and a user's genre and tag affinities belong to Layer 2. A deliberate
"favors this creator" boost may arrive later, but only as an explicit decision, never as an
accidental second count of the people Layer 1 already uses.

**Cold start is a base profile that encodes quality, never popularity.** A new user has no
ratings, so their profile starts from a base derived from what features correlate with high
TMDb *quality* across the catalog, a "typical viewer" prior, not a popularity chart. The
recommender's whole thesis is that popularity is the engagement metric it exists to avoid
(ADR-05), and the base must not smuggle it back in. Until a user rates anything they mostly
see Layer 1's honest graph under a light, neutral prior; as ratings arrive, the profile
personalizes away from that base.

**Interpretable weights, not embeddings, on purpose.** Because Layer 2 weights real, nameable
features, it can always explain itself: "ranked up because you rate shows with this creator
or genre highly." That explanation *is* the product (issue #7's context box). A learned
embedding, a black-box numeric fingerprint of a show, might rank marginally better but could
not say why, which would kill the one thing that makes TVLens TVLens. Here interpretability
is a requirement, not a nicety.

## After Action Review
The biggest thing today taught me is that the hard part of a recommender is the design, not
the code, and it is worth deciding before writing anything. Two ideas stuck with me. Keep the
layers honest: Layer 1 stays global and Layer 2 only re-ranks, so I never rebuild something
that already works. And keep it explainable, because the reason a show is recommended is the
product itself, which is why I chose nameable weights over a smarter black box I could not
explain.
