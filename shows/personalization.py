"""Layer 2 of the TVLens recommender: personalized re-ranking.

Layer 1 (recommenders.py) builds the global shared-people graph, materialized
and identical for everyone (ADR-07). Layer 2 takes one of those stored lists and
re-orders it to fit the signed-in user. It never rebuilds the graph and never
re-counts people: Layer 1 owns people, Layer 2 owns genre and tag preference, so
the same signal is never counted twice. See
docs/adr/08-layer2-personalized-reranking.md.

The whole layer is one linear, inspectable model. For each user we keep a signed
affinity per genre and per tag: a rating above neutral lifts that show's genres
and tags, a rating below neutral pushes them down (a 1-star rating is
information, it says "less of this"). A watched-but-unrated show is a weaker,
unsigned positive. The affinity is the AVERAGE signal a feature has earned, so it
stays a bounded "how much you like this" number rather than growing without limit
as a user rates more. A candidate's Layer-2 score is the plain sum of the
affinities of its genres and tags, so the re-rank can always explain itself in
nameable features rather than a black-box embedding (issue #7). A brand-new user
has no ratings, so their affinities are just a light quality prior derived from
TMDb vote_average, never popularity (ADR-05): they mostly see Layer 1's honest
order until their own ratings move it.

This module also builds the two personal home-page rows, because both answer
"what should this particular user see": Top Picks, the user's own ratings ranked
by lift over a global baseline (#15), and Side Quests, strong Layer 1 edges that
cross a genre line (ADR-09, docs/adr/09-side-quests-cross-genre-edges.md).
Neither is a chart: no row ever ranks by popularity, and no row ever shows a
user what other users are watching.
"""

from django.db.models import Avg, Count

from .models import Genre, Rating, Show, ShowTag, SimilarShow
from .recommenders import RankedShows

# A rating at NEUTRAL_SCORE says nothing about taste; above it is a positive
# signal, below it a negative one. 3.0 is the conventional middle of a 5-star
# scale and sits below the ">= 4 stars is a favorite" line the home page uses.
NEUTRAL_SCORE = 3.0

# A watched-but-unrated show is a weak positive with no score to sign it, so it
# counts as a small fixed signal on its genres and tags (ADR-08). Far below the
# pull of an explicit rating, so a deliberate rating always outweighs a view.
WATCHED_SIGNAL = 0.5

# How hard the cold-start quality prior is allowed to pull. Kept small on purpose
# (ADR-08 calls it a "light, neutral prior"): a new user should mostly see Layer
# 1's order, only gently tilted toward higher-quality genres, and every rating
# they add quickly outweighs it.
BASE_PRIOR_STRENGTH = 0.5

# Layer 1's order is the backbone of the re-rank. Each position in the stored
# list is worth this much "gravity" before preference is added, so a cold-start
# user (whose preference is near zero) keeps essentially Layer 1's order, and
# preference has to be worth more than a rank-step to move a candidate a place.
# On top of that gravity we add the candidate's Layer 1 score, which is large for
# a dominant edge (a spinoff sharing its whole cast) and ~0 for the long tail of
# near-tied neighbors: so a blowout edge resists personalization while the tail,
# which Layer 1 is nearly indifferent about, reorders freely to taste. (Fallback
# modes store score 0, ADR-05, leaving pure rank gravity there.)
RANK_STEP = 1.0

# A single dial for how strongly taste re-ranks. 1.0 means one star-step of genre
# affinity is worth one rank of Layer 1 gravity. Raise it to personalize harder,
# lower it to keep closer to Layer 1.
PREFERENCE_WEIGHT = 1.0


def _catalog_quality_prior():
    """A signed genre affinity from TMDb quality, not popularity (ADR-08 cold start).

    Each genre's mean vote_average minus the catalog mean, so a genre whose shows
    are rated higher than average gets a small positive prior and a weaker genre a
    small negative one. vote_average is a quality signal; popularity is the
    engagement metric this recommender exists to avoid, and it is never read here.
    """
    catalog_mean = Show.objects.aggregate(a=Avg("vote_average"))["a"] or 0.0
    prior = {}
    for gid, gavg in (
        Genre.objects.annotate(a=Avg("shows__vote_average")).values_list("id", "a")
    ):
        if gavg is not None:
            prior[gid] = gavg - catalog_mean
    return prior


class PreferenceProfile:
    """A user's Layer 2 affinities: signed, per genre and per tag, and inspectable.

    genre_weights and tag_weights are the effective affinities that rank
    candidates: the light quality prior plus everything the user's ratings and
    views have learned on top. learned_genre_weights / learned_tag_weights isolate
    just the user's own signal, so the "why" can say "because you rate Crime
    highly" without the prior muddying it (issue #7). rating_count == 0 is the
    cold-start case: the effective affinities are the prior alone.
    """

    def __init__(
        self,
        genre_weights,
        tag_weights,
        learned_genre_weights,
        learned_tag_weights,
        rating_count,
    ):
        self.genre_weights = genre_weights
        self.tag_weights = tag_weights
        self.learned_genre_weights = learned_genre_weights
        self.learned_tag_weights = learned_tag_weights
        self.rating_count = rating_count

    @property
    def is_cold_start(self):
        return self.rating_count == 0

    def score_for(self, show, show_tags=None):
        """How well one candidate's genres and tags line up with these affinities.

        A plain sum of the affinities of the show's genres plus its tags scaled by
        relevance. show.genres is read from the prefetch the Layer 1 list already
        carries, so scoring the whole list adds no per-candidate query.
        """
        total = 0.0
        for genre in show.genres.all():
            total += self.genre_weights.get(genre.id, 0.0)
        if show_tags:
            for tag_id, relevance in show_tags:
                total += self.tag_weights.get(tag_id, 0.0) * relevance
        return total

    def top_genres(self, limit=5):
        """The user's strongest learned genre affinities as (name, weight), signed.

        The interpretable readout the re-rank stands on: what the user's own
        ratings say, largest magnitude first. Empty for a cold-start user, whose
        affinities are the shared quality prior rather than anything personal.
        """
        if not self.learned_genre_weights:
            return []
        names = dict(
            Genre.objects.filter(id__in=self.learned_genre_weights).values_list(
                "id", "name"
            )
        )
        ranked = sorted(
            self.learned_genre_weights.items(), key=lambda kv: -abs(kv[1])
        )
        return [(names.get(gid, ""), weight) for gid, weight in ranked[:limit]]


def _mean(sums, counts):
    return {key: sums[key] / counts[key] for key in sums}


def build_profile(user):
    """Assemble a user's PreferenceProfile: the light quality prior, then learned.

    A learned affinity is the AVERAGE signal a genre (or tag) has earned across
    the user's rated and watched shows, so it is bounded to roughly one rating's
    worth however many shows they rate: rating ten crime shows highly says the
    same "I like crime" as rating one, not ten times as loud. A rating signs by
    score - NEUTRAL; a watched-but-unrated show is a weak WATCHED_SIGNAL positive.
    Anonymous or ratingless users get the prior alone (the cold-start ordering).
    A few bulk queries, no per-show work.
    """
    prior = _catalog_quality_prior()
    genre_weights = {gid: BASE_PRIOR_STRENGTH * w for gid, w in prior.items()}
    tag_weights = {}
    learned_genre = {}
    learned_tag = {}
    rating_count = 0

    if user is not None and user.is_authenticated:
        rated = dict(
            Rating.objects.filter(user=user).values_list("show_id", "score")
        )
        rating_count = len(rated)

        # Watched-but-unrated shows: a weak, unsigned positive. watched_by (ADR-08)
        # already counts a rated show as watched, so subtract the rated set to
        # avoid stacking a second, weaker signal on top of the real rating.
        watched_only = set(
            Show.objects.watched_by(user).values_list("id", flat=True)
        ) - set(rated)

        signal_by_show = {sid: score - NEUTRAL_SCORE for sid, score in rated.items()}
        for sid in watched_only:
            signal_by_show[sid] = WATCHED_SIGNAL

        if signal_by_show:
            show_ids = list(signal_by_show)
            g_sum, g_count = {}, {}
            for show_id, genre_id in (
                Show.genres.through.objects.filter(show_id__in=show_ids)
                .values_list("show_id", "genre_id")
            ):
                signal = signal_by_show[show_id]
                g_sum[genre_id] = g_sum.get(genre_id, 0.0) + signal
                g_count[genre_id] = g_count.get(genre_id, 0) + 1
            learned_genre = _mean(g_sum, g_count)

            # Tags weight their signal by relevance; the affinity is the
            # relevance-weighted mean, again bounded to about one rating's worth.
            t_sum, t_relsum = {}, {}
            for show_id, tag_id, relevance in (
                ShowTag.objects.filter(show_id__in=show_ids).values_list(
                    "show_id", "tag_id", "relevance"
                )
            ):
                signal = signal_by_show[show_id]
                t_sum[tag_id] = t_sum.get(tag_id, 0.0) + signal * relevance
                t_relsum[tag_id] = t_relsum.get(tag_id, 0.0) + relevance
            learned_tag = {
                tid: t_sum[tid] / t_relsum[tid] for tid in t_sum if t_relsum[tid]
            }

    for gid, w in learned_genre.items():
        genre_weights[gid] = genre_weights.get(gid, 0.0) + w
    for tid, w in learned_tag.items():
        tag_weights[tid] = tag_weights.get(tid, 0.0) + w

    return PreferenceProfile(
        genre_weights, tag_weights, learned_genre, learned_tag, rating_count
    )


def rerank(user, ranked):
    """Re-order a Layer 1 RankedShows for `user`, preserving its mode and rows.

    Each candidate's final score is Layer 1 gravity (its rank plus its Layer 1
    score) plus the user's genre/tag preference. A cold-start user keeps
    essentially Layer 1's order; a rated user sees the near-tied tail reorder to
    taste while a dominant edge (large Layer 1 score) holds. The sort is stable,
    so equal scores fall back to Layer 1's order.

    Returns a new RankedShows carrying the same Show objects (their Layer 1
    `score` and `shared_people` intact), each annotated with `.preference` and
    `.layer2_score`. The result also carries `.profile` and `.personalized`
    (True once the user's own ratings exist), so the page can show it was
    tailored.
    """
    profile = build_profile(user)
    n = len(ranked)

    tags_by_show = {}
    if n:
        for show_id, tag_id, relevance in (
            ShowTag.objects.filter(show_id__in=[s.id for s in ranked]).values_list(
                "show_id", "tag_id", "relevance"
            )
        ):
            tags_by_show.setdefault(show_id, []).append((tag_id, relevance))

    for position, show in enumerate(ranked):
        gravity = (n - position) * RANK_STEP + (show.score or 0.0)
        show.preference = PREFERENCE_WEIGHT * profile.score_for(
            show, tags_by_show.get(show.id)
        )
        show.layer2_score = gravity + show.preference

    reordered = sorted(ranked, key=lambda s: -s.layer2_score)
    result = RankedShows(reordered, mode=ranked.mode)
    result.profile = profile
    result.personalized = not profile.is_cold_start
    return result


# A Top Pick must actually be liked: the user's own score has to clear this
# floor before lift is even considered. 3.5 sits just above the scale's
# comfortable middle, so a show the user was lukewarm on can never headline
# their picks no matter how far it beats the baseline.
TOP_PICK_FLOOR = 3.5

# How many OTHER users must have rated a show before their average is a
# believable baseline. Below this the TVLens-side number is one or two
# opinions, so the benchmark falls back to TMDb instead.
MIN_OTHER_RATERS = 3


def top_picks(user, limit=12):
    """The user's rated shows, ranked by lift over a global baseline (#15).

    Top Picks is NOT the user's raw ratings replayed, and NOT a public
    leaderboard. Each rated show is measured against a benchmark of what
    everyone else thinks, and the list orders by lift = the user's score minus
    that baseline: the shows the user rates genuinely above the crowd rise to
    the top, while a 4.0 on a show the whole world rates 4.5 sinks. The
    baseline is a yardstick only; it is never displayed front-and-center.

    The baseline per show is the TVLens all-user average EXCLUDING this user,
    but only once at least MIN_OTHER_RATERS other people have rated it;
    thinner than that, it falls back to TMDb vote_average / 2 (TMDb rates on
    0-10, TVLens on 0.5-5). With a single user in the database, every show
    takes the TMDb fallback, which is what makes the demo meaningful today:
    lift reads as "how much more I liked this than the world did".

    Only shows the user scored >= TOP_PICK_FLOOR qualify; ties break on the
    user's score, then name, so equal lifts order deterministically. Returns
    up to `limit` Show objects, each annotated with `.user_score`,
    `.baseline`, and `.lift` so the ranking can always explain itself.
    Anonymous users get an empty list (the template keeps its empty state).
    """
    if user is None or not user.is_authenticated:
        return []

    rated = list(
        Rating.objects.filter(user=user, score__gte=TOP_PICK_FLOOR)
        .select_related("show")
        .prefetch_related("show__genres")
    )
    if not rated:
        return []

    others = {
        row["show_id"]: row
        for row in (
            Rating.objects.filter(show_id__in=[r.show_id for r in rated])
            .exclude(user=user)
            .values("show_id")
            .annotate(avg=Avg("score"), n=Count("id"))
        )
    }

    picks = []
    for r in rated:
        show = r.show
        other = others.get(r.show_id)
        if other and other["n"] >= MIN_OTHER_RATERS:
            baseline = other["avg"]
        else:
            baseline = show.vote_average / 2
        show.user_score = r.score
        show.baseline = baseline
        show.lift = r.score - baseline
        picks.append(show)

    picks.sort(key=lambda s: (-s.lift, -s.user_score, s.name))
    return picks[:limit]


# ── Side Quests (#10) ────────────────────────────────────────────────────────

# A seed is a show the user rated at least this highly. 4.0 is the same "high"
# line the home page already treats as a favorite genre and that ADR-08 calls
# the signal Layer 2 personalizes from, so the glowing genre pills and the shows
# Side Quests walks out from agree about what "you like this" means.
SIDE_QUEST_SEED_FLOOR = 4.0


class SideQuests(list):
    """The Side Quests row: shows, plus one fact about how the row was built.

    personalized is True when at least one entry was reached from a show this
    user rated highly, False for the catalog-wide walk an anonymous or unrated
    visitor sees. Mirrors RankedShows: a plain list with one attribute, so
    callers that expect a list keep working.
    """

    def __init__(self, shows=(), personalized=False):
        super().__init__(shows)
        self.personalized = personalized


def _genre_ids_by_show():
    """{show_id: set of genre_id}, in one query over the m2m through table."""
    by_show = {}
    for show_id, genre_id in (
        Show.genres.through.objects.order_by("show_id").values_list(
            "show_id", "genre_id"
        )
    ):
        by_show.setdefault(show_id, set()).add(genre_id)
    return by_show


def _cross_genre_edges(genres_by_show):
    """Layer 1 edges whose two ends share no genre at all, strongest first.

    This is the whole idea of Side Quests (ADR-09): not a new score, just the
    subset of the materialized graph (ADR-07) that already crosses a genre line.
    The edge is Layer 1's, so a pick is always something the people graph
    believes in; the genre disjointness is what makes it a side quest rather
    than more of the same.

    Both ends must actually carry genres. Every show in the catalog does today,
    but an import that lands a show with none would otherwise make every one of
    its edges look "cross-genre" on a technicality and flood the row.

    Read whole and filtered in Python: at catalog scale this is ~1k rows and one
    query, and the disjointness test does not express cleanly in the ORM.
    Ordering is explicit (Show and SimilarShow both carry a different Meta
    default; Show's is -popularity, which ADR-05 forbids ranking by).
    """
    edges = []
    for source_id, target_id, score in (
        SimilarShow.objects.order_by("-score", "target__name", "source__name")
        .values_list("source_id", "target_id", "score")
    ):
        source_genres = genres_by_show.get(source_id)
        target_genres = genres_by_show.get(target_id)
        if not source_genres or not target_genres:
            continue
        if source_genres & target_genres:
            continue
        edges.append((target_id, source_id, score))
    return edges


def side_quests(user, limit=12, exclude_ids=()):
    """The Side Quests row: strong Layer 1 edges that cross a genre line (#10).

    Plausibly likeable, but not what you would have picked yourself. Two paths,
    the same as the rest of the site (ADR-08): a neutral prior at cold start,
    personalizing as ratings arrive.

      cold start  the catalog's strongest cross-genre edges, whoever is looking.
                  Anonymous visitors get this too; the row needs no ratings, so
                  it renders for everyone (ADR-09).
      seeded      once the user has rated something >= SIDE_QUEST_SEED_FLOOR,
                  the walk starts from those shows: the cross-genre neighbors of
                  what they already like, strongest edge first.

    A seeded row is topped up from the catalog-wide walk when the user's own
    seeds run dry (a seed can have no cross-genre edge at all, and a seed whose
    genre list is broad often does), so the row is always full rather than a
    stub of four cards. Order is personal first: every seeded pick comes before
    every top-up even when the top-up edge scores higher, because a neighbor of
    a show this user liked is a better side quest than a stronger edge between
    two shows they have never touched. Shows the user has already
    watched never appear, and neither does anything in `exclude_ids` (the home
    page passes Top Picks, one show one row).

    Returns a SideQuests list of Show objects, each annotated with `.quest_from`
    (the show whose edge reached it) and `.quest_score` (that edge's Layer 1
    score), so the row can always say why a pick is there.
    """
    blocked = set(exclude_ids)
    seed_ids = set()
    if user is not None and user.is_authenticated:
        # No quest you have already been on: watched covers rated (ADR-08).
        blocked |= set(
            Show.objects.watched_by(user).order_by("id").values_list("id", flat=True)
        )
        seed_ids = set(
            Rating.objects.filter(user=user, score__gte=SIDE_QUEST_SEED_FLOOR)
            .order_by("show_id")
            .values_list("show_id", flat=True)
        )

    edges = _cross_genre_edges(_genre_ids_by_show())
    chosen = []
    taken = set()

    def take(candidates):
        for target_id, source_id, score in candidates:
            if len(chosen) >= limit:
                return
            if target_id in blocked or target_id in taken:
                continue
            taken.add(target_id)
            chosen.append((target_id, source_id, score))

    if seed_ids:
        take([e for e in edges if e[1] in seed_ids])
    # Anything the user's own shows reached makes this row personal; the top-up
    # below fills the rest of the strip and does not change that.
    personalized = bool(chosen)
    take(edges)

    if not chosen:
        return SideQuests()

    # One fetch for the picks and the shows they came from. Explicitly ordered:
    # Show's Meta default is -popularity, and nothing in this path may rank by
    # it (ADR-05). The order that matters is `chosen`, applied below.
    wanted = {t for t, _, _ in chosen} | {s for _, s, _ in chosen}
    by_id = {
        show.id: show
        for show in Show.objects.filter(pk__in=wanted)
        .prefetch_related("genres")
        .order_by("pk")
    }

    quests = []
    for target_id, source_id, score in chosen:
        show = by_id[target_id]
        show.quest_from = by_id[source_id]
        show.quest_score = score
        quests.append(show)
    return SideQuests(quests, personalized=personalized)
