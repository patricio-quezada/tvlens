"""Tests for the recommender's decided invariants.

Each test freezes one decision recorded in ADR-04 (and the 2026-08-06 review),
so a later layer that reweights this edge, or an ingest change that shifts the
data, fails loudly here instead of silently reranking the catalog.
"""

import math
from io import StringIO

from django.contrib.auth.models import AnonymousUser, User
from django.core.management import call_command
from django.db.models import Max
from django.test import TestCase
from django.urls import reverse

from .ingestion import MIN_VOTE_COUNT, Ingestor
from .models import (
    CastMember,
    CrewMember,
    Episode,
    Genre,
    Network,
    Person,
    Rating,
    Review,
    Season,
    Show,
    ShowTag,
    SimilarShow,
    Tag,
    WatchHistory,
    Watchlist,
)
from .personalization import (
    MIN_CONNECTION_TYPE_EDGES,
    MIN_CONNECTION_TYPE_LEAN,
    SIDE_QUEST_CENTRALITY_EXPONENT,
    SIDE_QUEST_HOP_DECAY,
    SIDE_QUEST_SEED_FLOOR,
    TOP_PICK_FLOOR,
    PreferenceProfile,
    build_profile,
    rated_shows,
    rerank,
    side_quests,
    top_picks,
    watch_next,
    without_watched,
)
from .recommenders import (
    INVOLVEMENT_EXPONENT,
    SQLITE_MAX_VARS_SAFE,
    RankedShows,
    compose_callout,
    connection_type,
    name_connections,
    role_index,
    role_indexes,
    shared_connections,
    similar_by_cast,
    similar_by_crew,
    similar_by_people,
    stored_similar,
)
from .search import ParsedQuery, _episode_fts, _fts_query, _word
from .search import search as run_search
from .views import DETAIL_RECOMMENDATION_LIMIT


class SimilarByPeopleTests(TestCase):
    """ADR-04's decisions, frozen against a fixture with a real spread of shares.

    The fixture this replaced gave both shows 10 episodes and nearly every
    credit episode_count=10, so almost every share was exactly 1.0. Since
    1.0 ** anything is 1.0, the suite was very nearly blind to the involvement
    weight it exists to protect: measured 2026-08-26, only two assertions in
    248 moved when INVOLVEMENT_EXPONENT went from 1.0 to 1.375. A fixture that
    cannot see the thing it freezes is worse than no fixture, so A and B now
    have unequal, unround runs and the shares below span 0.01 to 1.0 the way
    the catalog's do.

    Tests that freeze the min rule assert against INVOLVEMENT_EXPONENT rather
    than a baked float, because what they protect is which end the edge takes,
    not what the exponent happens to be. The exponent's own value is pinned in
    InvolvementExponentTests.
    """

    @classmethod
    def setUpTestData(cls):
        cls.a = Show.objects.create(tmdb_id=1, name="A", number_of_episodes=100)
        cls.b = Show.objects.create(tmdb_id=2, name="B", number_of_episodes=40)
        cls.p = Person.objects.create(tmdb_id=1, name="Lead")

    def test_min_rule_takes_weaker_end(self):
        # All 100 episodes of A (share 1.0), 10 of B's 40 (share 0.25). The
        # edge is worth the weaker end, so 0.25 and never 1.0.
        CastMember.objects.create(show=self.a, person=self.p, episode_count=100)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=10)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 0.25 ** INVOLVEMENT_EXPONENT)  # fmt: skip
        self.assertLess(r.score, 1.0)

    def test_involvement_weight_is_superlinear_not_proportional(self):
        # The whole point of ADR-04's amendment: a quarter-share is worth
        # LESS than a quarter of a full share, so thin ties cannot accumulate
        # their way past a real one. A linear weight would score exactly 0.25.
        CastMember.objects.create(show=self.a, person=self.p, episode_count=100)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=10)
        [r] = similar_by_people(self.a)
        self.assertLess(r.score, 0.25)

    def test_person_counted_once_at_best_count_across_cast_and_crew(self):
        # Cast says 20 of A, crew says all 100. The best count wins, so the
        # source share is 1.0 and the edge is B's 0.25. Taking the cast count
        # instead would score min(0.2, 0.25) = 0.2 and this would fail.
        CastMember.objects.create(show=self.a, person=self.p, episode_count=20)
        CrewMember.objects.create(show=self.a, person=self.p, job="Director", episode_count=100)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=10)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 0.25 ** INVOLVEMENT_EXPONENT)  # fmt: skip
        self.assertEqual(r.shared_people, 1)

    def test_null_episode_count_is_shared_but_weighs_zero(self):
        CastMember.objects.create(show=self.a, person=self.p, episode_count=None)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=40)
        [r] = similar_by_people(self.a)
        self.assertEqual((r.score, r.shared_people), (0.0, 1))

    def test_service_job_excluded_on_candidate_side(self):
        CrewMember.objects.create(show=self.a, person=self.p, job="Writer", episode_count=100)
        CrewMember.objects.create(show=self.b, person=self.p, job="Casting", episode_count=40)
        self.assertEqual(similar_by_people(self.a), [])

    def test_service_job_variant_excluded(self):
        # The 2026-08-06 review found eight casting variants leaking past the
        # original list. Freeze one of them.
        CrewMember.objects.create(show=self.a, person=self.p, job="Writer", episode_count=100)
        CrewMember.objects.create(
            show=self.b, person=self.p, job="Extras Casting", episode_count=40
        )
        self.assertEqual(similar_by_people(self.a), [])

    def test_facility_job_excluded(self):
        # ADR-01 amended 2026-08-26: the casting argument covers any credit
        # that links a facility's slate rather than two shows. A colorist who
        # graded the full run of both put Marvel's Daredevil first on
        # Elementary; under the involvement exponent that one credit decides
        # the ranking, so the widened list has to hold.
        CrewMember.objects.create(show=self.a, person=self.p, job="Writer", episode_count=100)
        CrewMember.objects.create(show=self.b, person=self.p, job="Colorist", episode_count=40)
        self.assertEqual(similar_by_people(self.a), [])

    def test_composing_is_not_a_facility_job(self):
        # The counterpart to the test above, and the line the widened list
        # draws: music SERVICE is excluded, composition is not. A score is
        # authorial, and MARQUEE_JOBS already treats it as show-defining.
        CrewMember.objects.create(
            show=self.a,
            person=self.p,
            job="Original Music Composer",
            episode_count=100,
        )
        CrewMember.objects.create(
            show=self.b,
            person=self.p,
            job="Original Music Composer",
            episode_count=40,
        )
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 1.0)

    def test_ratio_caps_at_one(self):
        # Credit rollups drift ahead of the episode total on returning series
        # (595 credited episodes of a 594-episode run). Nobody made more than
        # all of a show. Both sides overflow here, so both cap and the edge is
        # a full 1.0 rather than something above it.
        CastMember.objects.create(show=self.a, person=self.p, episode_count=105)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=44)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 1.0)

    def test_zero_number_of_episodes_yields_zero_score_not_crash(self):
        self.a.number_of_episodes = 0
        self.a.save()
        CastMember.objects.create(show=self.a, person=self.p, episode_count=50)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=20)
        [r] = similar_by_people(self.a)
        self.assertEqual(r.score, 0.0)

    def test_real_edge_outranks_popular_null_edge(self):
        # A candidate with a genuine episode-weighted edge must beat a more
        # popular candidate whose only link is a series-level (null) credit.
        # Freezes both the zero-weight decision and the popularity tie-break.
        popular = Show.objects.create(
            tmdb_id=3, name="Popular", number_of_episodes=40, popularity=99.0
        )
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        CastMember.objects.create(show=self.a, person=self.p, episode_count=50)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=20)
        CastMember.objects.create(show=self.a, person=ghost, episode_count=None)
        CastMember.objects.create(show=popular, person=ghost, episode_count=None)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["B", "Popular"])

    def test_show_with_no_qualifying_people_returns_empty_list(self):
        self.assertEqual(similar_by_people(self.a), [])

    def test_zero_episode_source_estimates_from_candidate_side(self):
        # Revised in ADR-05 (2026-08-07): a zero-episode source still knows
        # half of every edge, the candidate's side, so the list ranks by that
        # estimate. The three candidates are rigged so estimate order (Lead,
        # Half, Cameo), popularity order (Cameo first), and rating order
        # (Cameo first) all disagree; only the estimate explains the result.
        self.a.number_of_episodes = 0
        self.a.save()
        lead = Show.objects.create(
            tmdb_id=3,
            name="Lead",
            number_of_episodes=10,
            vote_average=6.0,
            vote_count=100,
            popularity=1.0,
        )
        half = Show.objects.create(
            tmdb_id=4,
            name="Half",
            number_of_episodes=10,
            vote_average=7.0,
            vote_count=100,
            popularity=50.0,
        )
        cameo = Show.objects.create(
            tmdb_id=5,
            name="Cameo",
            number_of_episodes=10,
            vote_average=9.9,
            vote_count=9000,
            popularity=99.0,
        )
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=lead, person=self.p, episode_count=10)
        CastMember.objects.create(show=half, person=self.p, episode_count=5)
        CastMember.objects.create(show=cameo, person=self.p, episode_count=1)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["Lead", "Half", "Cameo"])
        self.assertEqual(results.mode, "estimated")
        # The estimate carries the same involvement weight as the score
        # (ADR-04, amended 2026-08-26). Both rungs rank by how much of a show
        # a person is, so they have to mean the same thing by it; weighting
        # only the top rung would leave the ladder disagreeing with itself.
        # The exponent is monotonic, so it cannot change this order and only a
        # value assertion can catch its absence here.
        by_name = {s.name: s for s in results}
        self.assertAlmostEqual(by_name["Half"].estimate, 0.5 ** INVOLVEMENT_EXPONENT)  # fmt: skip
        self.assertAlmostEqual(by_name["Cameo"].estimate, 0.1 ** INVOLVEMENT_EXPONENT)  # fmt: skip
        self.assertLess(by_name["Cameo"].estimate, 0.1)

    def test_estimate_ties_break_on_rating_then_votes(self):
        # Equal estimates fall to the rating tie-break: vote_average
        # descending, then vote_count so a 10.0 on three votes cannot beat
        # an 8.9 on ten thousand. Popularity would order it Loud, Ties,
        # Grail; the rule orders it Grail, Ties, Loud.
        self.a.number_of_episodes = 0
        self.a.save()
        grail = Show.objects.create(
            tmdb_id=3,
            name="Grail",
            number_of_episodes=10,
            vote_average=9.5,
            vote_count=9000,
            popularity=1.0,
        )
        ties = Show.objects.create(
            tmdb_id=4,
            name="Ties",
            number_of_episodes=10,
            vote_average=8.9,
            vote_count=10000,
            popularity=50.0,
        )
        loud = Show.objects.create(
            tmdb_id=5,
            name="Loud",
            number_of_episodes=10,
            vote_average=8.9,
            vote_count=3,
            popularity=99.0,
        )
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        for other in (grail, ties, loud):
            CastMember.objects.create(show=other, person=self.p, episode_count=5)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["Grail", "Ties", "Loud"])
        self.assertEqual(results.mode, "estimated")

    def test_rating_mode_when_no_edge_carries_any_signal(self):
        # Every shared edge is a null-count series credit, so neither the
        # weighted score nor the candidate-side estimate has signal. The last
        # rung ranks by TMDb rating, per the original ADR-05 decision.
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        low = Show.objects.create(
            tmdb_id=3,
            name="Low",
            number_of_episodes=10,
            vote_average=6.0,
            vote_count=100,
            popularity=99.0,
        )
        high = Show.objects.create(
            tmdb_id=4,
            name="High",
            number_of_episodes=10,
            vote_average=9.0,
            vote_count=100,
            popularity=1.0,
        )
        CastMember.objects.create(show=self.a, person=ghost, episode_count=None)
        CastMember.objects.create(show=low, person=ghost, episode_count=None)
        CastMember.objects.create(show=high, person=ghost, episode_count=None)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["High", "Low"])
        self.assertEqual(results.mode, "rating")

    def test_weighted_path_ignores_rating_when_any_score_is_real(self):
        # The ladder must not leak upward: one real edge on the board and the
        # order is score then popularity, even when the zero-scored candidate
        # has the better rating.
        rated = Show.objects.create(
            tmdb_id=3,
            name="Rated",
            number_of_episodes=10,
            vote_average=9.9,
            vote_count=10000,
            popularity=99.0,
        )
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.a, person=ghost, episode_count=None)
        CastMember.objects.create(show=rated, person=ghost, episode_count=None)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["B", "Rated"])
        self.assertEqual(results.mode, "weighted")


class InvolvementExponentTests(TestCase):
    """The acceptance case for ADR-04's amendment: mass must not beat quality.

    This is the entire reason Layer 1 was rescored on 2026-08-26, and nothing
    in the suite checked it before. 217,622 of the catalog's 278,632 cast rows
    carry episode_count = 1. Under the original linear sum, 300 of those guests
    at 0.01 each totalled 3.0 and beat a genuine full-run co-lead at 1.0, so a
    show you share one star with lost to a show you share a crowd of extras
    with. Catalog-wide, 463 pairs in the stored top-12 lists were ordered that
    way.

    The exponent is chosen so this case is won with room, not won barely.
    Break-even for 300 guests at one episode of a hundred is 1.24, so 1.25
    would clear it by 1.05x and land the decision on a coin edge; 1.375 clears
    it by 1.87x. Going higher is worse rather than safer: past about 1.4 the
    score collapses toward whoever holds the single strongest tie and the
    mid-strength tail stops carrying register, which at 1.5 makes Silent
    Witness recommend Loki and costs The Pitt its edge to ER.
    """

    @classmethod
    def setUpTestData(cls):
        cls.seed = Show.objects.create(tmdb_id=1, name="Seed", number_of_episodes=100)
        # One shared co-lead, the whole run of both shows.
        cls.quality = Show.objects.create(
            tmdb_id=2, name="Quality", number_of_episodes=100, popularity=1.0
        )
        colead = Person.objects.create(tmdb_id=1, name="Co-Lead")
        CastMember.objects.create(show=cls.seed, person=colead, episode_count=100)
        CastMember.objects.create(show=cls.quality, person=colead, episode_count=100)

        # Three hundred shared guests, one episode each on both sides.
        cls.crowd = Show.objects.create(
            tmdb_id=3, name="Crowd", number_of_episodes=100, popularity=99.0
        )
        guests = Person.objects.bulk_create(
            Person(tmdb_id=100 + i, name=f"Guest {i}") for i in range(300)
        )
        CastMember.objects.bulk_create(
            [CastMember(show=cls.seed, person=g, episode_count=1) for g in guests]
            + [CastMember(show=cls.crowd, person=g, episode_count=1) for g in guests]
        )

    def test_one_full_run_colead_outranks_three_hundred_one_episode_guests(self):
        results = similar_by_people(self.seed)
        self.assertEqual([s.name for s in results], ["Quality", "Crowd"])

    def test_the_crowd_is_really_a_crowd(self):
        # Guards the fixture rather than the rule: if these numbers drift, the
        # test above stops demonstrating anything. 300 shared people at a
        # linear 0.01 apiece is the 3.0 that used to beat the co-lead's 1.0.
        crowd = next(s for s in similar_by_people(self.seed) if s.name == "Crowd")
        self.assertEqual(crowd.shared_people, 300)
        self.assertAlmostEqual(300 * 0.01, 3.0)

    def test_acceptance_margin_is_at_least_one_point_eight(self):
        # Measured 1.87x at INVOLVEMENT_EXPONENT = 1.375. Asserting the margin
        # rather than the ordering is what makes a future exponent change fail
        # loudly here: 1.25 would still pass the ordering test above, at 1.05x,
        # which is not a margin.
        by_name = {s.name: s for s in similar_by_people(self.seed)}
        margin = by_name["Quality"].score / by_name["Crowd"].score
        self.assertGreater(margin, 1.8)

    def test_exponent_stays_inside_the_measured_plateau(self):
        # Both bounds are measurements, not taste. Below 1.24 the acceptance
        # case above is lost outright. Above roughly 1.4 the head-to-head goes
        # the other way: of the 16 sources whose top pick differs between 1.375
        # and 1.5, about ten are better at 1.375 and one is better at 1.5.
        self.assertGreater(INVOLVEMENT_EXPONENT, 1.24)
        self.assertLessEqual(INVOLVEMENT_EXPONENT, 1.4)

    def test_a_thin_edge_is_shrunk_but_never_zeroed(self):
        # ADR-04's rejected alternatives ruled out a hard cutoff because it
        # "creates a cliff, throws away thin shows, and hurts short-form
        # content". The exponent is a curve, not a cliff, and ADR-05 needs
        # every real edge to stay real: an edge driven to exactly 0.0 would
        # drop its source a rung, changing the candidate set and not just the
        # order. Measured across all 37,950 candidate edges in the catalog,
        # none reaches 0.0; the smallest is 9.6e-06.
        thin = Show.objects.create(tmdb_id=4, name="Thin", number_of_episodes=100)
        walk_on = Person.objects.create(tmdb_id=9000, name="Walk On")
        CastMember.objects.create(show=self.seed, person=walk_on, episode_count=1)
        CastMember.objects.create(show=thin, person=walk_on, episode_count=1)
        edge = next(s for s in similar_by_people(self.seed) if s.name == "Thin")
        self.assertGreater(edge.score, 0.0)
        self.assertLess(edge.score, 0.01)


class SlugTests(TestCase):
    """The public identifier decided on ADR-03: filled from the name, unique,
    and stable once set so a link never rots."""

    def test_slug_autofilled_from_name_on_save(self):
        s = Show.objects.create(tmdb_id=100, name="Breaking Bad")
        self.assertEqual(s.slug, "breaking-bad")

    def test_slug_collision_gets_numeric_suffix(self):
        a = Show.objects.create(tmdb_id=101, name="The Office")
        b = Show.objects.create(tmdb_id=102, name="The Office")
        c = Show.objects.create(tmdb_id=103, name="The Office")
        self.assertEqual([a.slug, b.slug, c.slug], ["the-office", "the-office-2", "the-office-3"])

    def test_slug_is_stable_when_name_changes(self):
        s = Show.objects.create(tmdb_id=104, name="Original Name")
        original = s.slug
        s.name = "A Completely Different Name"
        s.save()
        self.assertEqual(s.slug, original)


class SharedConnectionsTests(TestCase):
    """The show-detail page's "why": the shared people, named and ordered by
    the same episode-share that ranked them (issue #2 wireframe)."""

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        cls.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=10)
        cls.lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        cls.maker = Person.objects.create(tmdb_id=2, name="The Maker")
        cls.extra = Person.objects.create(tmdb_id=3, name="Bit Player")

    def _connections(self):
        return shared_connections(
            self.source,
            role_index(self.source),
            self.cand,
            role_index(self.cand),
        )

    def test_contribution_orders_edges_and_count_matches_recommender(self):
        # Lead is in all of both (1.0); the extra shares one episode (0.1).
        CastMember.objects.create(
            show=self.source, person=self.lead, order=0, character="Hero", episode_count=10
        )
        CastMember.objects.create(
            show=self.cand, person=self.lead, order=0, character="Hero", episode_count=10
        )
        CastMember.objects.create(
            show=self.source, person=self.extra, order=600, character="Waiter", episode_count=1
        )
        CastMember.objects.create(
            show=self.cand, person=self.extra, order=600, character="Waiter", episode_count=1
        )
        conns = self._connections()
        self.assertEqual([c.name for c in conns], ["Lead Actor", "Bit Player"])
        self.assertAlmostEqual(conns[0].contribution, 1.0)
        # len equals the recommender's shared_people, both dedupe by person.
        [ranked] = similar_by_people(self.source)
        self.assertEqual(len(conns), ranked.shared_people)

    def test_named_leads_with_cast_names_marquee_and_counts_the_rest(self):
        CastMember.objects.create(
            show=self.source, person=self.lead, order=0, character="Hero", episode_count=10
        )
        CastMember.objects.create(
            show=self.cand, person=self.lead, order=0, character="Hero", episode_count=10
        )
        CrewMember.objects.create(
            show=self.source, person=self.maker, job="Creator", episode_count=10
        )
        CrewMember.objects.create(
            show=self.cand, person=self.maker, job="Creator", episode_count=10
        )
        CastMember.objects.create(
            show=self.source, person=self.extra, order=600, character="Waiter", episode_count=1
        )
        CastMember.objects.create(
            show=self.cand, person=self.extra, order=600, character="Waiter", episode_count=1
        )
        named, others = name_connections(self._connections())
        # Recognizable actor first (by character), then the marquee creator.
        self.assertEqual([c.name for c in named], ["Lead Actor", "The Maker"])
        self.assertEqual(named[0].kind, "cast")
        self.assertEqual(named[0].role, "Hero")
        self.assertEqual(named[1].role, "Creator")
        # The bit player collapses into the count.
        self.assertEqual(others, 1)

    def test_recognizable_actor_named_by_character_even_when_also_crew(self):
        # A lead who also directed an episode is still named by their role,
        # not the directing credit (pitch by cast).
        CastMember.objects.create(
            show=self.source, person=self.lead, order=0, character="Hero", episode_count=10
        )
        CrewMember.objects.create(
            show=self.source, person=self.lead, job="Director", episode_count=2
        )
        CastMember.objects.create(
            show=self.cand, person=self.lead, order=0, character="Hero", episode_count=10
        )
        [c] = self._connections()
        self.assertEqual((c.kind, c.role), ("cast", "Hero"))

    def test_falls_back_to_strongest_edges_when_nothing_is_prominent(self):
        # Only a shared bit player: no recognizable cast, no marquee crew.
        # The callout still names someone rather than a bare count.
        CastMember.objects.create(
            show=self.source, person=self.extra, order=600, character="Waiter", episode_count=5
        )
        CastMember.objects.create(
            show=self.cand, person=self.extra, order=600, character="Waiter", episode_count=5
        )
        named, others = name_connections(self._connections())
        self.assertEqual([c.name for c in named], ["Bit Player"])
        self.assertEqual(others, 0)

    def test_name_by_score_ranks_marquee_ahead_of_lower_scoring_cast(self):
        # A composer on every episode of both (1.0) outscores a recognizable
        # lead who only guested (0.2). Name-by-score names the composer first,
        # cast and crew merged in one order, not the cast first by prominence.
        CastMember.objects.create(
            show=self.source, person=self.lead, order=0, character="Hero", episode_count=2
        )
        CastMember.objects.create(
            show=self.cand, person=self.lead, order=0, character="Hero", episode_count=2
        )
        CrewMember.objects.create(
            show=self.source, person=self.maker, job="Original Music Composer", episode_count=10
        )
        CrewMember.objects.create(
            show=self.cand, person=self.maker, job="Original Music Composer", episode_count=10
        )
        named, others = name_connections(self._connections())
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])
        self.assertGreater(named[0].contribution, named[1].contribution)


class CalloutProseTests(TestCase):
    """The 7a callout: one flowing sentence per recommendation, with a
    data-driven lead, honest source-side episode context, roles in prose, and
    the long tail collapsed into a count (issue #2 wireframe)."""

    def setUp(self):
        # A short source and candidate so 'every episode' is easy to trigger.
        self.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=62)
        self.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=63)
        self._pid = 10

    def _person(self, name):
        self._pid += 1
        return Person.objects.create(tmdb_id=self._pid, name=name)

    def _cast(self, show, person, order, character, eps):
        CastMember.objects.create(
            show=show, person=person, order=order, character=character, episode_count=eps
        )

    def _crew(self, show, person, job, eps):
        CrewMember.objects.create(show=show, person=person, job=job, episode_count=eps)

    def _callout(self):
        conns = shared_connections(
            self.source,
            role_index(self.source),
            self.cand,
            role_index(self.cand),
        )
        named, others = name_connections(conns)
        return compose_callout(self.source, self.cand, conns, named, others)

    def _text(self, callout):
        return "".join(seg["v"] for seg in callout["segments"])

    def test_cast_lead_uses_source_side_episode_context(self):
        # A lead who did 43 of the source's 62 reads "across 43 episodes", not
        # the candidate's count, so the character and number agree on one show.
        lead = self._person("Bob Odenkirk")
        self._cast(self.source, lead, 0, "Saul Goodman", 43)
        self._cast(self.cand, lead, 0, "Saul Goodman", 63)
        text = self._text(self._callout())
        self.assertIn("Bob Odenkirk plays Saul Goodman across 43 episodes", text)

    def test_full_run_reads_every_episode(self):
        lead = self._person("Aaron Paul")
        self._cast(self.source, lead, 0, "Jesse Pinkman", 62)
        self._cast(self.cand, lead, 0, "Jesse Pinkman", 20)
        self.assertIn("plays Jesse Pinkman in all 62 episodes", self._text(self._callout()))

    def test_composer_on_every_episode_of_both_is_named_so(self):
        p = self._person("Dave Porter")
        self._crew(self.source, p, "Original Music Composer", 62)
        self._crew(self.cand, p, "Original Music Composer", 63)
        callout = self._callout()
        text = self._text(callout)
        # Crew-only callout capitalizes the opening role noun.
        self.assertTrue(text.startswith("Composer Dave Porter"))
        self.assertIn("scored every episode of both", text)

    def test_director_reads_with_source_episode_count(self):
        p = self._person("Tim Hunter")
        self._crew(self.source, p, "Director", 1)
        self._crew(self.cand, p, "Director", 1)
        self.assertIn("directed one episode", self._text(self._callout()))

    def test_no_lead_phrase_in_callout(self):
        # Editorial leads are gone (decided 2026-08-14): the callout carries no
        # 'lead' key and never prefixes a header, whatever the connection profile.
        maker = self._person("The Creator")  # would once have led "Made by..."
        self._crew(self.source, maker, "Creator", 62)
        self._crew(self.cand, maker, "Creator", 63)
        callout = self._callout()
        self.assertNotIn("lead", callout)
        text = self._text(callout)
        self.assertNotIn("Made by the same people", text)
        self.assertTrue(text.startswith("Creator The Creator"))

    def test_weak_tie_opens_on_the_connection_not_a_thinner_thread(self):
        # A single guest across a few episodes of a long run once earned an
        # "A thinner thread:" lead; now it opens straight on the person.
        guest = self._person("A Guest")
        self._cast(self.source, guest, 0, "Waiter", 3)
        self._cast(self.cand, guest, 0, "Waiter", 3)
        text = self._text(self._callout())
        self.assertNotIn("thinner thread", text)
        self.assertTrue(text.startswith("A Guest plays Waiter"))

    def test_all_cast_opens_on_the_strongest_actor_no_header(self):
        # An all-cast tie once led "N actors carry over:"; now it opens on the
        # strongest actor with no header.
        a = self._person("Actor One")
        b = self._person("Actor Two")
        self._cast(self.source, a, 0, "Hero", 62)
        self._cast(self.cand, a, 0, "Hero", 63)
        self._cast(self.source, b, 1, "Sidekick", 60)
        self._cast(self.cand, b, 1, "Sidekick", 60)
        text = self._text(self._callout())
        self.assertNotIn("carry over", text)
        self.assertTrue(text.startswith("Actor One plays Hero"))

    def test_tail_collapses_the_remainder_with_a_count(self):
        lead = self._person("Star")
        self._cast(self.source, lead, 0, "Hero", 62)
        self._cast(self.cand, lead, 0, "Hero", 63)
        for i in range(3):  # three bit players collapse into the tail
            bit = self._person(f"Bit {i}")
            self._cast(self.source, bit, 600 + i, "Extra", 2)
            self._cast(self.cand, bit, 600 + i, "Extra", 2)
        text = self._text(self._callout())
        self.assertTrue(text.rstrip().endswith("with 3 others."))

    def test_secondary_cast_grouped_not_repeated(self):
        lead = self._person("Star")
        second = self._person("Costar")
        self._cast(self.source, lead, 0, "Hero", 62)
        self._cast(self.cand, lead, 0, "Hero", 63)
        self._cast(self.source, second, 1, "Rival", 55)
        self._cast(self.cand, second, 1, "Rival", 55)
        text = self._text(self._callout())
        # The lead is elaborated; the second is gathered, not re-described.
        self.assertIn("Costar appears too", text)
        self.assertNotIn("Costar plays", text)

    def test_no_tail_when_everyone_is_named(self):
        lead = self._person("Star")
        self._cast(self.source, lead, 0, "Hero", 62)
        self._cast(self.cand, lead, 0, "Hero", 63)
        text = self._text(self._callout())
        self.assertNotIn("others", text)
        self.assertTrue(text.rstrip().endswith("episodes."))


class ShowDetailViewTests(TestCase):
    """The route, the 404, and that the page surfaces the named connection."""

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(
            tmdb_id=1,
            name="Source Show",
            number_of_episodes=10,
            tagline="A tagline.",
            vote_average=8.9,
            vote_count=1234,
        )
        cls.cand = Show.objects.create(tmdb_id=2, name="Candidate Show", number_of_episodes=10)
        lead = Person.objects.create(tmdb_id=1, name="Jane Star")
        CastMember.objects.create(
            show=cls.source, person=lead, order=0, character="The Detective", episode_count=10
        )
        CastMember.objects.create(
            show=cls.cand, person=lead, order=0, character="The Detective", episode_count=10
        )
        # The detail view now serves the ranking from the materialized store
        # (ADR-07), so populate it before exercising the page.
        call_command("rebuild_similar_shows", stdout=StringIO())

    def test_detail_page_renders_show_and_named_connection(self):
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Source Show", body)
        self.assertIn("A tagline.", body)
        self.assertIn("1,234 votes", body)
        self.assertIn("ordered by the people they share with this show", body)
        self.assertIn("Candidate Show", body)
        self.assertIn("Jane Star", body)
        self.assertIn("The Detective", body)

    def test_detail_url_uses_slug(self):
        self.assertEqual(self.source.get_absolute_url(), "/shows/source-show/")

    def test_unknown_slug_returns_404(self):
        self.assertEqual(self.client.get(reverse("shows:detail", args=["nope"])).status_code, 404)


class StoredSimilarTests(TestCase):
    """The materialized Layer 1 store (ADR-07): rebuild_similar_shows precomputes
    similar_by_people into SimilarShow, and stored_similar reads it back in the
    same shape. These freeze the one invariant that matters: for an unchanged
    catalog the store equals the live computation, row for row.
    """

    @classmethod
    def setUpTestData(cls):
        # A weighted graph: Src shares a full-run lead with B and a partial
        # player with C, so Src has two ranked edges. Lonely shares its one
        # person with no one, so it has zero edges.
        cls.src = Show.objects.create(tmdb_id=1, name="Src", number_of_episodes=10)
        cls.b = Show.objects.create(tmdb_id=2, name="Bshow", number_of_episodes=10, popularity=5.0)
        cls.c = Show.objects.create(tmdb_id=3, name="Cshow", number_of_episodes=10, popularity=1.0)
        cls.lonely = Show.objects.create(tmdb_id=9, name="Lonely", number_of_episodes=10)
        lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        side = Person.objects.create(tmdb_id=2, name="Side Player")
        hermit = Person.objects.create(tmdb_id=3, name="Hermit")
        # Lead: all of Src and all of B -> strong edge (1.0).
        CastMember.objects.create(
            show=cls.src, person=lead, order=0, character="Hero", episode_count=10
        )
        CastMember.objects.create(
            show=cls.b, person=lead, order=0, character="Hero", episode_count=10
        )
        # Side: all of Src, two episodes of C -> weaker edge (0.2).
        CastMember.objects.create(
            show=cls.src, person=side, order=1, character="Rival", episode_count=10
        )
        CastMember.objects.create(
            show=cls.c, person=side, order=1, character="Rival", episode_count=2
        )
        CastMember.objects.create(
            show=cls.lonely, person=hermit, order=0, character="Alone", episode_count=10
        )

        # A zero-episode source ranks by the candidate side: mode "estimated".
        cls.blank = Show.objects.create(tmdb_id=4, name="Blank", number_of_episodes=0)
        traveler = Person.objects.create(tmdb_id=4, name="Traveler")
        CastMember.objects.create(
            show=cls.blank, person=traveler, order=0, character="Wanderer", episode_count=5
        )
        CastMember.objects.create(
            show=cls.b, person=traveler, order=2, character="Wanderer", episode_count=5
        )

        call_command("rebuild_similar_shows", stdout=StringIO())

    def _assertMatchesLive(self, show):
        live = similar_by_people(show)
        stored = stored_similar(show)
        self.assertEqual(
            [(s.pk, round(s.score, 6), s.shared_people) for s in stored],
            [(s.pk, round(s.score, 6), s.shared_people) for s in live],
        )
        if live:  # mode is only meaningful when the list carries edges
            self.assertEqual(stored.mode, live.mode)

    def test_stored_matches_live_for_weighted_source(self):
        # Src's two edges arrive in the same order, with the same scores and
        # shared-people counts, as the live recommender.
        stored = stored_similar(self.src)
        self.assertEqual([s.name for s in stored], ["Bshow", "Cshow"])
        self.assertEqual(stored.mode, "weighted")
        self.assertAlmostEqual(stored[0].score, 1.0)
        # Side played 2 of Cshow's 10, so the edge is a 0.2 share carrying the
        # involvement weight (ADR-04, amended 2026-08-26). Asserted against the
        # constant because what the store owes the live recommender is the same
        # number, whatever that number is.
        self.assertAlmostEqual(stored[1].score, 0.2 ** INVOLVEMENT_EXPONENT)  # fmt: skip
        self._assertMatchesLive(self.src)

    def test_stored_matches_live_for_a_second_source(self):
        # B is a target above but also a source (shares Lead with Src,
        # Traveler with Blank); its own stored list must match live too.
        self._assertMatchesLive(self.b)

    def test_source_with_no_similar_shows_stores_nothing(self):
        self.assertEqual(SimilarShow.objects.filter(source=self.lonely).count(), 0)
        stored = stored_similar(self.lonely)
        self.assertEqual(list(stored), [])
        self.assertEqual(stored.mode, "weighted")

    def test_estimated_mode_round_trips_through_the_store(self):
        # Blank has zero episodes, so its edges score zero and the live ladder
        # falls to "estimated". That non-weighted mode must survive the store.
        live = similar_by_people(self.blank)
        self.assertEqual(live.mode, "estimated")
        stored = stored_similar(self.blank)
        self.assertEqual(stored.mode, "estimated")
        self.assertEqual([s.pk for s in stored], [s.pk for s in live])
        # The denormalized mode is written onto every one of the source's edges.
        modes = set(SimilarShow.objects.filter(source=self.blank).values_list("mode", flat=True))
        self.assertEqual(modes, {"estimated"})

    def test_detail_page_renders_from_the_stored_edges(self):
        # The detail view now reads stored_similar. With the store built, the
        # page must surface Src's top candidate and its shared-people count.
        resp = self.client.get(self.src.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Bshow", body)
        self.assertIn("Lead Actor", body)
        self.assertIn("ordered by the people they share with this show", body)

    def test_rebuild_is_wholesale_replacing_stale_edges(self):
        # A stale edge left by a prior build must not survive the next rebuild.
        SimilarShow.objects.create(
            source=self.lonely,
            target=self.src,
            rank=0,
            score=9.9,
            shared_people=1,
            mode="weighted",
        )
        call_command("rebuild_similar_shows", stdout=StringIO())
        self.assertEqual(SimilarShow.objects.filter(source=self.lonely).count(), 0)


class SqlVariableCeilingTests(TestCase):
    """Freeze the scale fix from issue #1: a person set larger than SQLite's
    variable ceiling must not raise OperationalError. Two shows share more
    people than SQLITE_MAX_VARS_SAFE, spanning several chunks, so a regression
    that dropped the subquery or the chunking would 500 here instead of live.
    """

    @classmethod
    def setUpTestData(cls):
        # Comfortably past two chunk boundaries so the people-side fold has to
        # merge across batches, and far past the old 999 SQLite floor.
        n = 2 * SQLITE_MAX_VARS_SAFE + 5
        cls.src = Show.objects.create(tmdb_id=1, name="Src", number_of_episodes=100)
        cls.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=100)
        Person.objects.bulk_create([Person(tmdb_id=1000 + i, name=f"P{i}") for i in range(n)])
        people = list(Person.objects.all())
        CastMember.objects.bulk_create(
            [CastMember(show=cls.src, person=p, episode_count=100) for p in people]
        )
        CastMember.objects.bulk_create(
            [CastMember(show=cls.cand, person=p, episode_count=100) for p in people]
        )
        CrewMember.objects.bulk_create(
            [CrewMember(show=cls.src, person=p, job="Writer", episode_count=100) for p in people]
        )
        CrewMember.objects.bulk_create(
            [CrewMember(show=cls.cand, person=p, job="Writer", episode_count=100) for p in people]
        )
        cls.n = n

    def test_similar_by_people_chunks_past_the_ceiling(self):
        [r] = similar_by_people(self.src, limit=5)
        self.assertEqual(r.pk, self.cand.pk)
        # Every person is on all 100 episodes of both, so each contributes 1.0
        # and all n register as shared; the chunked fold matches one big query.
        self.assertEqual(r.shared_people, self.n)
        self.assertAlmostEqual(r.score, float(self.n))

    def test_similar_by_cast_subquery_past_the_ceiling(self):
        result = list(similar_by_cast(self.src))
        self.assertEqual([s.pk for s in result], [self.cand.pk])
        self.assertEqual(result[0].shared_cast, self.n)

    def test_similar_by_crew_subquery_past_the_ceiling(self):
        result = list(similar_by_crew(self.src))
        self.assertEqual([s.pk for s in result], [self.cand.pk])
        self.assertEqual(result[0].shared_crew, self.n)


class RatingTests(TestCase):
    """The rating slice (#5): the half-star endpoint, its guards, and display.

    Each test freezes one decision behind the widget: ratings are per signed-in
    user (TVLens has real auth, so a rating keys off request.user and rating
    requires login), one row per (user, show) updated in place on a re-rate, and
    constrained to the MovieLens half-star scale (0.5 to 5.0 in 0.5 steps). The
    show's average is the mean of them. This is the cold-start data Layer 2 (#6)
    reads, so the stored shape is the contract.
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(tmdb_id=1, name="Rated Show", number_of_episodes=10)
        cls.other = Show.objects.create(tmdb_id=2, name="Other Show", number_of_episodes=10)
        cls.alice = User.objects.create_user("alice", password="pw-alice-123")
        cls.bob = User.objects.create_user("bob", password="pw-bob-123")

    def _rate(self, show, score):
        return self.client.post(reverse("shows:rate", args=[show.slug]), {"score": score})

    def _rate_in_place(self, show, score):
        return self.client.post(
            reverse("shows:rate", args=[show.slug]),
            {"score": score},
            headers={"x-requested-with": "fetch"},
        )

    def test_rating_in_place_answers_with_state_instead_of_redirecting(self):
        # ADR-10: the widget asked to stay where it is, so nothing navigates and
        # the response carries what the page needs to repaint itself.
        self.client.force_login(self.alice)
        resp = self._rate_in_place(self.show, "4.0")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["score"], 4.0)
        # The average sentence is rendered server-side from its own template, so
        # its wording is never duplicated in JavaScript.
        self.assertIn("★ 4.0", payload["meta_html"])
        self.assertIn("1 rating on TVLens", payload["meta_html"])
        self.assertEqual(Rating.objects.get(user=self.alice, show=self.show).score, 4.0)

    def test_rating_in_place_queues_no_flash_message(self):
        # Nothing navigates, so a queued message would have no page to land on
        # and would surface later somewhere unrelated.
        self.client.force_login(self.alice)
        self._rate_in_place(self.show, "4.0")
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertNotIn("You rated", body)

    def test_the_scale_guards_still_apply_in_place(self):
        # The endpoint is public whichever way it is called.
        self.client.force_login(self.alice)
        self.assertEqual(self._rate_in_place(self.other, "3.3").status_code, 400)
        self.assertFalse(Rating.objects.filter(show=self.other).exists())

    def test_recording_a_rating_persists_the_score(self):
        self.client.force_login(self.alice)
        resp = self._rate(self.show, "3.5")
        self.assertEqual(resp.status_code, 302)
        # Back to the widget, not the top of the page: the POST-redirect is a
        # fresh navigation, so without the fragment the stars land off-screen.
        self.assertTrue(resp["Location"].endswith("#rate"))
        self.assertEqual(Rating.objects.get(user=self.alice, show=self.show).score, 3.5)

    def test_re_rating_updates_the_row_and_does_not_duplicate(self):
        self.client.force_login(self.alice)
        self._rate(self.show, "2.0")
        self._rate(self.show, "4.5")
        rows = Rating.objects.filter(user=self.alice, show=self.show)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().score, 4.5)

    def test_scale_bounds_and_half_step_are_enforced(self):
        self.client.force_login(self.alice)
        # The endpoints of the scale are accepted.
        for good in ("0.5", "5.0"):
            self.assertEqual(self._rate(self.show, good).status_code, 302)
        # Below, above, off-the-half-step, and junk are rejected, no row written.
        for bad in ("0.4", "5.5", "3.3", "0", "-1", "abc", ""):
            self.assertEqual(self._rate(self.other, bad).status_code, 400)
        self.assertFalse(Rating.objects.filter(show=self.other).exists())

    def test_average_reflects_all_ratings(self):
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        Rating.objects.create(user=self.bob, show=self.show, score=3.0)
        self.assertEqual(self.show.average_rating, 3.5)

    def test_average_and_count_show_on_the_detail_page(self):
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        Rating.objects.create(user=self.bob, show=self.show, score=5.0)
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn("★ 4.5", body)
        self.assertIn("2 ratings", body)

    def test_widget_prechecks_the_users_current_rating(self):
        Rating.objects.create(user=self.alice, show=self.show, score=3.5)
        self.client.force_login(self.alice)
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        # 3.5 is the fourth button in the high-to-low widget (5.0, 4.5, 4.0,
        # 3.5). The stars are the submit, so there is no radio to be :checked --
        # the persisted fill starts from a server-rendered class (#18).
        self.assertIn('value="3.5" class="half chosen"', body)
        self.assertNotIn('class="rate-save"', body)
        # The score reads on the page at full size, and no longer only inside
        # the per-star title tooltip a touchscreen cannot summon (#12).
        self.assertIn('<span class="score-label">Your rating</span>★ 3.5', body)
        self.assertNotIn('title="3.5 stars"', body)

    def test_rating_requires_login(self):
        resp = self._rate(self.show, "3.0")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
        self.assertFalse(Rating.objects.filter(show=self.show).exists())

    def test_get_on_the_endpoint_is_not_allowed(self):
        self.client.force_login(self.alice)
        resp = self.client.get(reverse("shows:rate", args=[self.show.slug]))
        self.assertEqual(resp.status_code, 405)

    def test_anonymous_detail_page_prompts_login_not_widget(self):
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn("Log in", body)
        self.assertNotIn('class="star-rating"', body)


class RatingDeselectionTests(TestCase):
    """Taking a rating back, not only changing it.

    From the 2026-08-22 demo review: "I cannot deselect my rating so maybe we
    add the option as well. Maybe dragging it all the way to the beginning of
    the first star allows for deselection." The widget had ten ways to say a
    different thing and no way to say nothing, so a mis-click was permanent in
    the only sense that matters -- it kept feeding Top Picks and Layer 2.

    These freeze what clearing IS: deleting the row, not storing a zero. Zero is
    not on the MovieLens scale, and rate() still rejects it as a score, so the
    clear arrives as its own field. And they freeze that it degrades the whole
    way down: the control is a plain submit button, so it works with JavaScript
    off, and answers in place with the header when the script is there (ADR-10).
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(tmdb_id=1, name="Rated Show", number_of_episodes=10)
        cls.alice = User.objects.create_user("alice", password="pw-alice-123")
        cls.bob = User.objects.create_user("bob", password="pw-bob-123")

    def _url(self):
        return reverse("shows:rate", args=[self.show.slug])

    def _clear(self, **kwargs):
        return self.client.post(self._url(), {"clear": "1"}, **kwargs)

    def _clear_in_place(self):
        return self._clear(headers={"x-requested-with": "fetch"})

    def test_clearing_deletes_the_row_it_does_not_store_a_zero(self):
        # An absent rating and a rating of nothing are the same fact, and there
        # is one way to store it. A 0.0 row would be a score off the scale
        # sitting in the data Layer 2 reads (ADR-08).
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        self.client.force_login(self.alice)
        resp = self._clear()
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith("#rate"))
        self.assertFalse(Rating.objects.filter(user=self.alice).exists())

    def test_zero_is_still_not_a_score(self):
        # The clear is its own field precisely so this guard does not have to
        # move: the endpoint is public and VALID_SCORES is the contract.
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self._url(), {"score": "0"}).status_code, 400)
        self.assertFalse(Rating.objects.filter(show=self.show).exists())

    def test_clearing_leaves_everyone_elses_rating_alone(self):
        # The delete is keyed on (user, show), like every other write here.
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        Rating.objects.create(user=self.bob, show=self.show, score=2.0)
        self.client.force_login(self.alice)
        self._clear()
        self.assertEqual(Rating.objects.get(show=self.show).user_id, self.bob.id)

    def test_clearing_something_never_rated_is_harmless(self):
        # Nothing to delete is not an error. The widget can be in this state
        # whenever a second tab got there first.
        self.client.force_login(self.alice)
        self.assertEqual(self._clear().status_code, 302)
        self.assertFalse(Rating.objects.exists())

    def test_clearing_in_place_answers_with_a_null_score(self):
        # ADR-10's shape, on the clear path: nothing navigates, and the null is
        # how the script knows to empty the widget rather than light a star.
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        self.client.force_login(self.alice)
        resp = self._clear_in_place()
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIsNone(payload["score"])
        # Removing the last rating changes the average sentence entirely, so it
        # is re-rendered from its own template on this path too.
        self.assertIn("Not yet rated on TVLens", payload["meta_html"])
        self.assertFalse(Rating.objects.exists())

    def test_clearing_in_place_queues_no_flash_message(self):
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        self.client.force_login(self.alice)
        self._clear_in_place()
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertNotIn("Cleared your rating", body)

    def test_clearing_requires_login(self):
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        resp = self._clear()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
        self.assertTrue(Rating.objects.exists())

    def test_the_control_is_a_plain_submit_so_it_works_without_script(self):
        # The whole widget degrades: ten stars are ten submit buttons (#18) and
        # the clear is an eleventh. Asserting on the tag, not the word: the
        # inline script names the same field in a selector string.
        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        self.client.force_login(self.alice)
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn('<button type="submit" name="clear" value="1"', body)

    def test_the_control_hides_itself_when_there_is_nothing_to_clear(self):
        # Present but hidden rather than absent, so the widget keeps its width
        # and the stars do not shift sideways the moment a rating is saved.
        self.client.force_login(self.alice)
        unrated = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn('class="star-clear is-empty"', unrated)

        Rating.objects.create(user=self.alice, show=self.show, score=4.0)
        rated = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn('class="star-clear"', rated)
        self.assertNotIn('class="star-clear is-empty"', rated)

    def test_the_widget_reads_as_unrated_again_after_a_clear(self):
        # The round trip a user actually performs. Nothing is lit, and the
        # readout says so in the same words it used before they ever rated.
        Rating.objects.create(user=self.alice, show=self.show, score=3.5)
        self.client.force_login(self.alice)
        self._clear()
        resp = self.client.get(self.show.get_absolute_url())
        self.assertIsNone(resp.context["user_rating"])
        # On the context, not the body: `chosen` is also a CSS selector and a
        # string literal in the inline script, so a bare substring check would
        # match the page's own machinery rather than a lit star.
        self.assertFalse(any(s["chosen"] for s in resp.context["star_steps"]))
        self.assertIn(
            '<span class="score-label">Your rating</span>Not rated yet',
            resp.content.decode(),
        )

    def test_a_cleared_show_leaves_my_ratings(self):
        # The user's record is the mirror of what they have said (#11), so
        # unsaying something has to remove it from there too.
        Rating.objects.create(user=self.alice, show=self.show, score=3.5)
        self.client.force_login(self.alice)
        listed = self.client.get(reverse("shows:my_ratings"))
        self.assertIn(self.show, listed.context["shows"])
        self._clear()
        after = self.client.get(reverse("shows:my_ratings"))
        self.assertEqual(list(after.context["shows"]), [])


class WatchedSignalTests(TestCase):
    """ADR-08's "a rating implies watched", made queryable for Layer 2 (#6).

    Watched is derived, never stored: a show counts as watched for a user if
    they rated it OR logged any WatchHistory for one of its episodes. These
    freeze the three cases the rule turns on (rating-only, watch-history-only,
    untouched), that the bulk queryset dedupes, and the anonymous guard.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("viewer", password="pw-viewer-123")
        cls.rated = Show.objects.create(tmdb_id=1, name="Rated Only", number_of_episodes=10)
        cls.watched = Show.objects.create(tmdb_id=2, name="Watched Only", number_of_episodes=10)
        cls.untouched = Show.objects.create(tmdb_id=3, name="Untouched", number_of_episodes=10)

        # Rated but never played: the rating alone implies watched.
        Rating.objects.create(user=cls.user, show=cls.rated, score=4.0)

        # Played but never rated, across two episodes so a missing distinct()
        # would surface the watched show twice in the bulk queryset.
        season = Season.objects.create(show=cls.watched, tmdb_id=100, season_number=1)
        for i in (1, 2):
            ep = Episode.objects.create(season=season, tmdb_id=1000 + i, episode_number=i)
            WatchHistory.objects.create(user=cls.user, episode=ep)

    def test_rated_show_counts_as_watched_without_watch_history(self):
        self.assertTrue(self.rated.is_watched_by(self.user))

    def test_watch_history_only_show_counts_as_watched(self):
        self.assertTrue(self.watched.is_watched_by(self.user))

    def test_untouched_show_is_not_watched(self):
        self.assertFalse(self.untouched.is_watched_by(self.user))

    def test_watched_by_returns_both_signals_once_each(self):
        watched_pks = sorted(Show.objects.watched_by(self.user).values_list("pk", flat=True))
        self.assertEqual(watched_pks, sorted([self.rated.pk, self.watched.pk]))

    def test_anonymous_user_has_watched_nothing(self):
        anon = AnonymousUser()
        self.assertFalse(self.rated.is_watched_by(anon))
        self.assertEqual(list(Show.objects.watched_by(anon)), [])


class Layer2ProfileTests(TestCase):
    """The interpretable per-user weights Layer 2 ranks on (ADR-08).

    Weights are signed and nameable, not an embedding: a high rating lifts that
    show's genres, a low one pushes them down, and the readout can always say
    which genre and by how much. These freeze that the signal is signed and that
    the profile is inspectable. All shows share one vote_average so the flat
    quality prior does not mask the learned signal.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("cinephile", password="pw-cinephile-1")
        cls.comedy = Genre.objects.create(tmdb_id=1, name="Comedy")
        cls.horror = Genre.objects.create(tmdb_id=2, name="Horror")
        cls.com = Show.objects.create(
            tmdb_id=1, name="Com", number_of_episodes=10, vote_average=8.0
        )
        cls.com.genres.add(cls.comedy)
        cls.hor = Show.objects.create(
            tmdb_id=2, name="Hor", number_of_episodes=10, vote_average=8.0
        )
        cls.hor.genres.add(cls.horror)

    def test_high_rating_lifts_a_genre_low_rating_pushes_it_down(self):
        Rating.objects.create(user=self.user, show=self.com, score=5.0)
        Rating.objects.create(user=self.user, show=self.hor, score=1.0)
        profile = build_profile(self.user)
        # score - NEUTRAL (3.0): 5.0 -> +2.0 on Comedy, 1.0 -> -2.0 on Horror.
        self.assertAlmostEqual(profile.learned_genre_weights[self.comedy.id], 2.0)
        self.assertAlmostEqual(profile.learned_genre_weights[self.horror.id], -2.0)

    def test_cold_start_profile_carries_no_learned_signal(self):
        profile = build_profile(self.user)  # user has rated nothing
        self.assertTrue(profile.is_cold_start)
        self.assertEqual(profile.learned_genre_weights, {})
        self.assertEqual(profile.top_genres(), [])

    def test_top_genres_is_a_signed_named_readout(self):
        Rating.objects.create(user=self.user, show=self.com, score=5.0)
        Rating.objects.create(user=self.user, show=self.hor, score=1.0)
        top = dict(build_profile(self.user).top_genres())
        self.assertGreater(top["Comedy"], 0)
        self.assertLess(top["Horror"], 0)


class Layer2ColdStartTests(TestCase):
    """Cold start is a quality prior, never a popularity chart (ADR-05/ADR-08)."""

    def test_prior_favors_quality_and_ignores_popularity(self):
        good = Genre.objects.create(tmdb_id=1, name="Prestige")
        weak = Genre.objects.create(tmdb_id=2, name="Filler")
        # High quality but unpopular vs low quality but very popular. If the prior
        # leaned on popularity, Filler would win; it must not.
        g = Show.objects.create(
            tmdb_id=1,
            name="G",
            number_of_episodes=10,
            vote_average=9.0,
            popularity=1.0,
        )
        g.genres.add(good)
        b = Show.objects.create(
            tmdb_id=2,
            name="B",
            number_of_episodes=10,
            vote_average=6.0,
            popularity=999.0,
        )
        b.genres.add(weak)
        profile = build_profile(AnonymousUser())
        self.assertTrue(profile.is_cold_start)
        self.assertGreater(profile.genre_weights[good.id], 0)
        self.assertLess(profile.genre_weights[weak.id], 0)
        self.assertGreater(profile.genre_weights[good.id], profile.genre_weights[weak.id])


class Layer2RerankTests(TestCase):
    """Re-ranking a Layer 1 list per user (ADR-08): cold start leaves the order
    alone, a positive signal lifts a genre, a negative one sinks it, and Layer 1's
    facts survive. Candidates share one vote_average so the prior stays flat.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("viewer", password="pw-viewer-xyz")
        cls.comedy = Genre.objects.create(tmdb_id=1, name="Comedy")
        cls.drama = Genre.objects.create(tmdb_id=2, name="Drama")
        cls.a = Show.objects.create(
            tmdb_id=1,
            name="Alpha",
            number_of_episodes=10,
            vote_average=8.0,
            popularity=3.0,
        )
        cls.a.genres.add(cls.drama)
        cls.b = Show.objects.create(
            tmdb_id=2,
            name="Bravo",
            number_of_episodes=10,
            vote_average=8.0,
            popularity=2.0,
        )
        cls.b.genres.add(cls.comedy)
        cls.c = Show.objects.create(
            tmdb_id=3,
            name="Charlie",
            number_of_episodes=10,
            vote_average=8.0,
            popularity=1.0,
        )
        cls.c.genres.add(cls.comedy)
        cls.fave = Show.objects.create(
            tmdb_id=9, name="FaveComedy", number_of_episodes=10, vote_average=8.0
        )
        cls.fave.genres.add(cls.comedy)
        cls.fave_drama = Show.objects.create(
            tmdb_id=10, name="FaveDrama", number_of_episodes=10, vote_average=8.0
        )
        cls.fave_drama.genres.add(cls.drama)

    def _layer1(self):
        # A fixed Layer 1 order of near-tied tail edges (small, close scores): the
        # drama Alpha first, then the two comedies. Layer 1 barely distinguishes
        # them, so taste is free to reorder.
        order = [self.a, self.b, self.c]
        for i, s in enumerate(order):
            s.score = 0.3 - 0.1 * i
            s.shared_people = 2
        return RankedShows(order, mode="weighted")

    def test_cold_start_preserves_layer1_order(self):
        reranked = rerank(self.user, self._layer1())
        self.assertEqual([s.name for s in reranked], ["Alpha", "Bravo", "Charlie"])
        self.assertFalse(reranked.personalized)
        self.assertEqual(reranked.mode, "weighted")

    def test_positive_signal_lifts_the_preferred_genre(self):
        Rating.objects.create(user=self.user, show=self.fave, score=5.0)
        reranked = rerank(self.user, self._layer1())
        names = [s.name for s in reranked]
        self.assertEqual(names[0], "Bravo")  # a comedy overtakes the drama Alpha
        self.assertLess(names.index("Bravo"), names.index("Alpha"))
        self.assertTrue(reranked.personalized)

    def test_negative_signal_demotes_a_disliked_genre(self):
        Rating.objects.create(user=self.user, show=self.fave, score=0.5)
        reranked = rerank(self.user, self._layer1())
        names = [s.name for s in reranked]
        self.assertEqual(names[0], "Alpha")  # the drama rises as comedy is pushed down
        self.assertLess(names.index("Alpha"), names.index("Bravo"))

    def test_rerank_keeps_layer1_facts_and_mode(self):
        reranked = rerank(self.user, self._layer1())
        self.assertEqual(reranked.mode, "weighted")
        for s in reranked:
            self.assertEqual(s.shared_people, 2)
            self.assertTrue(hasattr(s, "score"))

    def test_dominant_layer1_edge_resists_personalization(self):
        # A blowout Layer 1 edge (a spinoff-strength score) must not be dislodged
        # by taste. The user dislikes drama, yet a dominant drama edge stays first
        # while the near-tied tail below it reorders.
        Rating.objects.create(user=self.user, show=self.fave_drama, score=0.5)
        dom = Show.objects.create(
            tmdb_id=20, name="Dominant", number_of_episodes=10, vote_average=8.0
        )
        dom.genres.add(self.drama)
        dom.score, dom.shared_people = 20.0, 5
        self.b.score, self.b.shared_people = 0.2, 1
        self.c.score, self.c.shared_people = 0.2, 1
        reranked = rerank(self.user, RankedShows([dom, self.b, self.c], mode="weighted"))
        self.assertEqual(reranked[0].name, "Dominant")


class Layer2DetailViewTests(TestCase):
    """End to end on the page a logged-in user actually sees: the detail view's
    'More shows like this' is Layer 1 for anonymous and re-ranked for a rated
    user, and the shift is visible in the rendered order and the caption.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("fan", password="pw-fan-abcde")
        drama = Genre.objects.create(tmdb_id=1, name="Drama")
        comedy = Genre.objects.create(tmdb_id=2, name="Comedy")
        cls.source = Show.objects.create(
            tmdb_id=1, name="Source", number_of_episodes=10, vote_average=8.0
        )
        # Two candidates share the same lead with Source, so Layer 1 scores them
        # equally and breaks the tie on popularity: DramaPick first.
        cls.drama_pick = Show.objects.create(
            tmdb_id=2,
            name="DramaPick",
            number_of_episodes=10,
            vote_average=8.0,
            popularity=5.0,
        )
        cls.drama_pick.genres.add(drama)
        cls.comedy_pick = Show.objects.create(
            tmdb_id=3,
            name="ComedyPick",
            number_of_episodes=10,
            vote_average=8.0,
            popularity=1.0,
        )
        cls.comedy_pick.genres.add(comedy)
        cls.fave = Show.objects.create(
            tmdb_id=9, name="FaveComedy", number_of_episodes=10, vote_average=8.0
        )
        cls.fave.genres.add(comedy)
        lead = Person.objects.create(tmdb_id=1, name="Shared Lead")
        for s in (cls.source, cls.drama_pick, cls.comedy_pick):
            CastMember.objects.create(
                show=s, person=lead, order=0, character="Hero", episode_count=10
            )
        call_command("rebuild_similar_shows", stdout=StringIO())

    def test_anonymous_sees_layer1_order(self):
        body = self.client.get(self.source.get_absolute_url()).content.decode()
        self.assertLess(body.index("DramaPick"), body.index("ComedyPick"))
        self.assertNotIn("reordered for", body)

    def test_rated_user_sees_recommendations_reordered_to_taste(self):
        Rating.objects.create(user=self.user, show=self.fave, score=5.0)
        self.client.force_login(self.user)
        body = self.client.get(self.source.get_absolute_url()).content.decode()
        # The comedy preference lifts ComedyPick above the DramaPick that Layer 1
        # ranked first, and the page says so.
        # The order is the evidence. The page used to say "reordered for you"
        # above the list; that line was removed as noise, so nothing announces
        # it any more.
        self.assertLess(body.index("ComedyPick"), body.index("DramaPick"))
        self.assertNotIn("reordered for", body)


class TopPicksTests(TestCase):
    """Top Picks (#15): the user's rated shows ranked by lift over a baseline.

    Each test freezes one decided rule: the order is lift (user score minus
    baseline), not raw stars; only shows rated >= 3.5 qualify; the baseline is
    the other-user average once MIN_OTHER_RATERS others have rated, else TMDb
    vote_average / 2; and the global number is a yardstick, never a leaderboard
    (another user's ratings shift a baseline but never inject their shows).
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("me", password="pw-me-12345")
        # TMDb-side quality differs so the fallback baselines differ: Beloved is
        # world-adored (4.5 baseline), Sleeper the world shrugs at (3.0).
        cls.beloved = Show.objects.create(
            tmdb_id=1, name="Beloved", number_of_episodes=10, vote_average=9.0
        )
        cls.sleeper = Show.objects.create(
            tmdb_id=2, name="Sleeper", number_of_episodes=10, vote_average=6.0
        )
        cls.meh = Show.objects.create(
            tmdb_id=3, name="Meh", number_of_episodes=10, vote_average=5.0
        )

    def test_ranked_by_lift_not_raw_score(self):
        # Raw stars say Beloved (5.0) > Sleeper (4.0); lift says Sleeper
        # (4.0 - 3.0 = +1.0) > Beloved (5.0 - 4.5 = +0.5).
        Rating.objects.create(user=self.user, show=self.beloved, score=5.0)
        Rating.objects.create(user=self.user, show=self.sleeper, score=4.0)
        picks = top_picks(self.user)
        self.assertEqual([s.name for s in picks], ["Sleeper", "Beloved"])
        self.assertAlmostEqual(picks[0].lift, 1.0)
        self.assertAlmostEqual(picks[1].lift, 0.5)

    def test_floor_excludes_shows_the_user_did_not_actually_like(self):
        # +1.5 lift, but a 3.0 is lukewarm: it must not be a Top Pick.
        Rating.objects.create(user=self.user, show=self.meh, score=3.0)
        self.assertEqual(top_picks(self.user), [])

    def test_other_user_average_becomes_baseline_past_the_minimum(self):
        # Three others average 3.0 on Beloved, so the TVLens baseline replaces
        # the 4.5 TMDb fallback and the lift grows from +0.5 to +2.0. The
        # user's own rating must not sneak into the baseline.
        Rating.objects.create(user=self.user, show=self.beloved, score=5.0)
        for i, score in enumerate((2.5, 3.0, 3.5)):
            other = User.objects.create_user(f"other{i}", password=f"pw-o-{i}00")
            Rating.objects.create(user=other, show=self.beloved, score=score)
        [pick] = top_picks(self.user)
        self.assertAlmostEqual(pick.baseline, 3.0)
        self.assertAlmostEqual(pick.lift, 2.0)

    def test_below_the_minimum_falls_back_to_tmdb(self):
        # Two other raters are not a believable TVLens baseline yet.
        Rating.objects.create(user=self.user, show=self.beloved, score=5.0)
        for i in range(2):
            other = User.objects.create_user(f"few{i}", password=f"pw-f-{i}00")
            Rating.objects.create(user=other, show=self.beloved, score=0.5)
        [pick] = top_picks(self.user)
        self.assertAlmostEqual(pick.baseline, 4.5)

    def test_not_a_leaderboard_other_users_shows_never_appear(self):
        stranger = User.objects.create_user("stranger", password="pw-s-99999")
        Rating.objects.create(user=stranger, show=self.beloved, score=5.0)
        self.assertEqual(top_picks(self.user), [])
        self.assertEqual(top_picks(AnonymousUser()), [])

    def test_home_page_renders_the_picks_row(self):
        Rating.objects.create(user=self.user, show=self.sleeper, score=4.5)
        self.client.force_login(self.user)
        body = self.client.get(reverse("shows:index")).content.decode()
        self.assertIn("Top Picks for me", body)
        self.assertIn("Sleeper", body)
        self.assertNotIn("Start rating", body)

    def test_a_top_pick_never_repeats_in_recently_added(self):
        # One show, one row: a show surfaced as a Top Pick must not also
        # appear in Recently Added (and Side Quests inherits this rule, issue #10).
        Rating.objects.create(user=self.user, show=self.sleeper, score=4.5)
        self.client.force_login(self.user)
        ctx = self.client.get(reverse("shows:index")).context
        self.assertIn(self.sleeper, ctx["top_picks"])
        self.assertNotIn(self.sleeper, list(ctx["recently_added"]))
        # Unrated shows still reach Recently Added untouched.
        self.assertIn(self.beloved, list(ctx["recently_added"]))
        # Anonymous visitors have no picks, so nothing is held back.
        self.client.logout()
        ctx = self.client.get(reverse("shows:index")).context
        self.assertIn(self.sleeper, list(ctx["recently_added"]))


class SideQuestsTests(TestCase):
    """Side Quests (#10, ADR-09 amended): surprise measured against taste.

    A side quest is a show that is surprising FOR THIS USER, and surprise needs
    an expectation to violate. These freeze what that means: the row is gated on
    the user's own ratings, it is built only from their own favorites' strong
    edges, and a pick has to land in a genre they have never rated highly. The
    Layer 1 graph is written straight into SimilarShow here, because that table
    is exactly Layer 2's input (ADR-07) and explicit ranks are what the strength
    gate is about.
    """

    @classmethod
    def setUpTestData(cls):
        crime = Genre.objects.create(tmdb_id=1, name="Crime")
        drama = Genre.objects.create(tmdb_id=2, name="Drama")
        comedy = Genre.objects.create(tmdb_id=3, name="Comedy")
        scifi = Genre.objects.create(tmdb_id=4, name="SciFi")
        western = Genre.objects.create(tmdb_id=5, name="Western")
        animation = Genre.objects.create(tmdb_id=6, name="Animation")

        def show(tmdb_id, name, genres, popularity=1.0):
            s = Show.objects.create(
                tmdb_id=tmdb_id,
                name=name,
                number_of_episodes=10,
                popularity=popularity,
            )
            s.genres.set(genres)
            return s

        # Three crime dramas. Rated highly, they are what this user has
        # demonstrated: Crime and Drama are the expectation everything else is
        # measured against.
        cls.alpha = show(1, "Alpha", [crime, drama])
        cls.bravo = show(2, "Bravo", [crime, drama])
        cls.charlie = show(3, "Charlie", [crime, drama])
        cls.echo = show(4, "Echo", [crime, drama])

        cls.fully_new = show(5, "FullyNew", [scifi, western])
        cls.strong_partial = show(6, "StrongPartial", [crime, drama, comedy])
        cls.same_only = show(7, "SameOnly", [crime, drama], popularity=999.0)
        cls.weak_new = show(8, "WeakNew", [animation], popularity=800.0)
        cls.deep_new = show(9, "DeepNew", [western])
        cls.delta = show(10, "Delta", [scifi])
        cls.outsider = show(11, "Outsider", [comedy])
        cls.bystander = show(12, "Bystander", [western], popularity=500.0)

        def edge(source, target, rank, score):
            SimilarShow.objects.create(
                source=source,
                target=target,
                rank=rank,
                score=score,
                shared_people=1,
                mode="weighted",
            )

        edge(cls.alpha, cls.strong_partial, 0, 1.8)  # big edge, one new genre
        edge(cls.alpha, cls.fully_new, 1, 0.8)  # smaller edge, all new
        edge(cls.alpha, cls.delta, 2, 0.3)  # new, until it is watched
        edge(cls.bravo, cls.same_only, 0, 5.0)  # the graph's best edge
        edge(cls.charlie, cls.weak_new, 4, 0.05)  # all new, barely an edge
        edge(cls.charlie, cls.deep_new, 7, 2.0)  # all new, but rank 7
        edge(cls.outsider, cls.bystander, 0, 9.0)  # nothing to do with us

    def _user(self, name, *seeds, score=5.0):
        user = User.objects.create_user(name, password="x")
        for seed in seeds:
            Rating.objects.create(user=user, show=seed, score=score)
        return user

    def _rater(self, name="rater"):
        return self._user(name, self.alpha, self.bravo, self.charlie)

    def _names(self, quests):
        return [s.name for s in quests]

    # ── the gate ────────────────────────────────────────────────────────────

    def test_anonymous_visitors_get_no_row_and_no_locked_copy(self):
        # An anonymous visitor has demonstrated nothing, so nothing can be
        # surprising to them. The old catalog-wide row was one list identical
        # for every visitor; it is gone (ADR-09 amended), and the locked copy is
        # only ever shown to someone who can act on it.
        quests = side_quests(AnonymousUser())
        self.assertEqual(list(quests), [])
        self.assertFalse(quests.locked)
        body = self.client.get(reverse("shows:index")).content.decode()
        self.assertNotIn("Side Quests", body)
        self.assertNotIn("Side quests locked", body)

    def test_two_seeds_are_not_enough(self):
        # One or two shows say what a person watched, not what they lean
        # toward, so there is still no expectation to violate.
        user = self._user("two", self.alpha, self.bravo)
        quests = side_quests(user)
        self.assertEqual(list(quests), [])
        self.assertTrue(quests.locked)

    def test_three_seeds_unlock_the_row(self):
        quests = side_quests(self._rater())
        self.assertTrue(quests)
        self.assertFalse(quests.locked)

    def test_ratings_below_the_seed_floor_do_not_unlock(self):
        # Three ratings, all watched and all above ADR-08's neutral 3.0, but
        # none of them is "rated highly", so none of them demonstrates a taste.
        user = self._user(
            "lukewarm",
            self.alpha,
            self.bravo,
            self.charlie,
            score=SIDE_QUEST_SEED_FLOOR - 0.5,
        )
        self.assertTrue(side_quests(user).locked)

    def test_the_locked_row_shows_the_exact_copy(self):
        self.client.force_login(self._user("newcomer"))
        response = self.client.get(reverse("shows:index"))
        # The gate is not "three shows", it is three shows rated at or above
        # the seed floor, and the copy has to say the rule the code enforces.
        self.assertContains(
            response,
            "Side quests locked. Rate three shows 4 stars or higher to unlock.",
        )
        self.assertContains(response, "0 of 3 logged")

    def test_an_unlocked_user_with_nothing_new_gets_no_row_at_all(self):
        # This user is past the gate, but their own favorites reach only shows
        # they have already watched. No cards, and crucially NOT the locked
        # copy: telling someone who has rated three shows to rate three shows
        # would be a lie.
        user = self._user("stuck", self.bravo, self.same_only, self.echo)
        quests = side_quests(user)
        self.assertEqual(list(quests), [])
        self.assertFalse(quests.locked)
        self.client.force_login(user)
        body = self.client.get(reverse("shows:index")).content.decode()
        self.assertNotIn("Side quests locked", body)

    # ── what makes a pick a side quest ──────────────────────────────────────

    def test_every_pick_lands_in_a_genre_the_user_has_never_rated_highly(self):
        # The invariant the row is named for, now measured against the person
        # rather than against the pick's own source.
        user = self._rater()
        demonstrated = {"Crime", "Drama"}
        for pick in side_quests(user):
            new = {g.name for g in pick.genres.all()} - demonstrated
            self.assertTrue(new, f"{pick.name} is all genres this user knows")
            self.assertEqual({g.name for g in pick.quest_new_genres}, new)

    def test_a_genre_carried_by_one_seed_is_not_as_familiar_as_one_carried_by_all(self):
        """The defect this grading exists to fix.

        Every seed here is Crime and Drama, so both sit at familiarity 1.0.
        Nothing carries a genre held by only some seeds, so this asserts the
        shape directly: a show whose genres the user knows completely scores
        zero novelty and cannot appear, while a partially familiar one still
        can. Under the old set-membership rule the two were the same answer.
        """
        user = self._rater()
        names = self._names(side_quests(user))
        # Every genre known -> novelty exactly 0.0 -> never chosen.
        self.assertNotIn("SameOnly", names)
        # Two thirds known -> penalised, not deleted.
        self.assertIn("StrongPartial", names)

    def test_the_graphs_strongest_edge_is_not_a_side_quest(self):
        # SameOnly is reached by the biggest edge in the catalog (5.0) from a
        # show this user loves, and it is the most popular show here. It is
        # still more crime drama, so it is not a surprise and never appears.
        self.assertNotIn("SameOnly", self._names(side_quests(self._rater())))

    def test_distance_can_beat_a_stronger_edge(self):
        # StrongPartial's edge (1.8) dwarfs FullyNew's (0.8), but two thirds of
        # StrongPartial is more of what this user already likes. Surprise is
        # strength TIMES distance: 0.8 x 1.00 beats 1.8 x 0.33.
        quests = side_quests(self._rater())
        names = self._names(quests)
        self.assertEqual(names[0], "FullyNew")
        # The claim is FullyNew over StrongPartial, not FullyNew over the whole
        # field. Which show takes second is a matter of how hard familiarity is
        # graded (SIDE_QUEST_GENRE_EXPONENT), and pinning it here would freeze a
        # constant this test is not about.
        self.assertLess(names.index("FullyNew"), names.index("StrongPartial"))
        strong = next(q for q in quests if q.name == "StrongPartial")
        self.assertGreater(strong.quest_score, quests[0].quest_score)

    def test_distance_alone_does_not_win(self):
        # WeakNew is as far from this taste as a show can get, on an edge of
        # 0.05. Strangeness on no evidence is noise, so it sits last.
        self.assertEqual(self._names(side_quests(self._rater()))[-1], "WeakNew")

    def test_only_the_strong_half_of_a_seeds_list_is_walked(self):
        # DeepNew is fully novel AND sits on a 2.0 edge, which would put it
        # first. It is rank 7 in Charlie's stored list, past the strength gate,
        # so it never enters: a confident connection is part of the definition.
        self.assertNotIn("DeepNew", self._names(side_quests(self._rater())))

    def test_only_the_users_own_favorites_are_walked(self):
        # Outsider -> Bystander is a 9.0 edge into a genre this user has never
        # rated, and it is the second most popular show here. It is not reachable
        # from anything they like, so it is not their side quest. There is no
        # global path left for it to arrive on.
        self.assertNotIn("Bystander", self._names(side_quests(self._rater())))

    def test_watched_shows_never_come_back_as_a_quest(self):
        # Delta is new in genre and sits on a 1.0 edge from Alpha, but this user
        # has already seen it (a rating implies watched, ADR-08).
        user = self._rater()
        Rating.objects.create(user=user, show=self.delta, score=2.0)
        self.assertNotIn("Delta", self._names(side_quests(user)))

    def test_the_row_is_not_a_popularity_ordering(self):
        # ADR-05: nothing here is a chart. The two most popular shows in this
        # catalog are exactly the two the row refuses, and what is left runs
        # against popularity rather than with it.
        quests = side_quests(self._rater())
        self.assertNotIn("SameOnly", self._names(quests))
        self.assertNotIn("Bystander", self._names(quests))
        by_popularity = sorted(quests, key=lambda s: -s.popularity)
        self.assertNotEqual(self._names(quests), self._names(by_popularity))

    def test_quest_carries_the_edge_that_earned_it(self):
        # Each pick can say why it is there: which favorite reached it, on what
        # Layer 1 score, into which genres that are new to this user.
        quest = side_quests(self._rater())[0]
        self.assertEqual(quest.quest_from, self.alpha)
        self.assertAlmostEqual(quest.quest_score, 0.8)
        # quest_score stays Layer 1's own number; quest_surprise is the ordering
        # product, and strength is log-compressed so novelty can outweigh it.
        self.assertAlmostEqual(quest.quest_surprise, math.log1p(0.8))
        self.assertEqual(quest.quest_hops, 1)
        self.assertEqual(quest.quest_reach, 1)
        self.assertEqual(sorted(g.name for g in quest.quest_new_genres), ["SciFi", "Western"])

    def test_limit_takes_the_most_surprising(self):
        """limit slices the top off the full ordering, it does not re-rank."""
        user = self._rater()
        self.assertEqual(
            self._names(side_quests(user, limit=2)),
            self._names(side_quests(user))[:2],
        )

    # ── the row on the page ─────────────────────────────────────────────────

    def test_dedupe_priority_top_picks_then_side_quests_then_recently_added(self):
        # Priority is by how personal the row is, and deliberately NOT by render
        # order: Side Quests sits below Recently added on the page but claims its
        # shows first (ADR-09).
        user = self._rater("home")
        self.client.force_login(user)
        response = self.client.get(reverse("shows:index"))

        picks = {s.pk for s in response.context["top_picks"]}
        quests = {s.pk for s in response.context["side_quests"]}
        recent = {s.pk for s in response.context["recently_added"]}
        self.assertTrue(picks)
        self.assertTrue(quests)
        self.assertFalse(picks & quests)
        self.assertFalse(picks & recent)
        self.assertFalse(quests & recent)

    def test_an_unlocked_row_renders_its_cards(self):
        self.client.force_login(self._rater("render"))
        response = self.client.get(reverse("shows:index"))
        self.assertContains(response, "Side Quests")
        self.assertContains(response, "FullyNew")
        self.assertNotContains(response, "Side quests locked")


class SideQuestsRankingTests(TestCase):
    """What orders the row (ADR-09, amended twice).

    The first amendment walked one hop and ordered by score x novelty. That made
    the candidate pool identical to the seeds' own recommendation lists, and
    because raw Layer 1 scores span an order of magnitude while novelty is a
    share bounded at 1, the strongest edge in the pool won on strength alone.
    The row was the recommendation row wearing a different title.

    These freeze the three terms that fixed it: strength is log-compressed so
    novelty can actually outweigh it, the walk goes a second hop at a discount
    so distance is a real axis, and a show that several seeds all reach is
    pushed down as central to the taste rather than on its edge.
    """

    @classmethod
    def setUpTestData(cls):
        crime = Genre.objects.create(tmdb_id=1, name="Crime")
        drama = Genre.objects.create(tmdb_id=2, name="Drama")
        mystery = Genre.objects.create(tmdb_id=3, name="Mystery")
        western = Genre.objects.create(tmdb_id=4, name="Western")
        animation = Genre.objects.create(tmdb_id=5, name="Animation")

        def show(tmdb_id, name, genres):
            s = Show.objects.create(tmdb_id=tmdb_id, name=name, number_of_episodes=10)
            s.genres.set(genres)
            return s

        home = [crime, drama, mystery]
        cls.seed_a = show(1, "SeedA", home)
        cls.seed_b = show(2, "SeedB", home)
        cls.seed_c = show(3, "SeedC", home)

        # Strength vs novelty. Blockbuster sits on the biggest edge here but is
        # three quarters what the user already watches; Modest is a fifth of
        # that edge and entirely new.
        cls.blockbuster = show(4, "Blockbuster", home + [western])
        cls.modest = show(5, "Modest", [western, animation])

        # Distance. Bridge is all demonstrated genres, so it is never a quest
        # itself, but it is a real connection and the walk goes through it.
        cls.bridge = show(6, "Bridge", [crime, drama])
        cls.far_new = show(7, "FarNew", [animation])
        cls.near_new = show(8, "NearNew", [animation])

        # Centrality. Identical edges and identical novelty; the only
        # difference is how many seeds find them.
        cls.everyone = show(9, "Everyone", [western])
        cls.only_one = show(10, "OnlyOne", [western])

        def edge(source, target, rank, score):
            SimilarShow.objects.create(
                source=source,
                target=target,
                rank=rank,
                score=score,
                shared_people=1,
                mode="weighted",
            )

        edge(cls.seed_a, cls.blockbuster, 0, 5.0)
        edge(cls.seed_a, cls.modest, 1, 0.8)

        edge(cls.seed_b, cls.bridge, 0, 3.0)
        edge(cls.bridge, cls.far_new, 0, 3.0)
        edge(cls.seed_b, cls.near_new, 1, 3.0)

        edge(cls.seed_a, cls.everyone, 2, 2.0)
        edge(cls.seed_b, cls.everyone, 2, 2.0)
        edge(cls.seed_c, cls.everyone, 0, 2.0)
        edge(cls.seed_c, cls.only_one, 1, 2.0)

    def _rater(self, name="rater"):
        user = User.objects.create_user(name, password="x")
        for seed in (self.seed_a, self.seed_b, self.seed_c):
            Rating.objects.create(user=user, show=seed, score=5.0)
        return user

    def _order(self, quests):
        return [s.name for s in quests]

    def _rank_of(self, quests, name):
        return self._order(quests).index(name)

    # ── strength is compressed so novelty can win ───────────────────────────

    def test_a_blockbuster_edge_no_longer_outranks_a_novel_one(self):
        # Blockbuster's edge is 5.0 against Modest's 0.8, but only one of its
        # four genres is new to this user while all of Modest's are. Multiplying
        # the raw scores put Blockbuster first (5.0 x 0.25 = 1.25 beats
        # 0.8 x 1.00); log1p compresses strength onto novelty's scale, so
        # 1.79 x 0.25 = 0.45 now loses to 0.59. This is the ordering that made
        # the row read as a recommendation list.
        quests = side_quests(self._rater("cmp"))
        self.assertLess(self._rank_of(quests, "Modest"), self._rank_of(quests, "Blockbuster"))

    def test_strength_still_counts_for_something(self):
        # Compression is not erasure: between two fully novel shows, the one on
        # the better edge still wins. NearNew (3.0) outranks Modest (0.8), and
        # both are 100% new.
        quests = side_quests(self._rater("still"))
        self.assertLess(self._rank_of(quests, "NearNew"), self._rank_of(quests, "Modest"))

    # ── the walk goes a second hop, at a discount ───────────────────────────

    def test_a_show_two_hops_out_can_be_a_quest(self):
        # FarNew is reachable only as SeedB -> Bridge -> FarNew. Under the
        # one-hop walk it did not exist as a candidate at all.
        quests = side_quests(self._rater("far"))
        self.assertIn("FarNew", self._order(quests))
        pick = next(s for s in quests if s.name == "FarNew")
        self.assertEqual(pick.quest_hops, 2)
        self.assertEqual(pick.quest_from, self.seed_b)

    def test_a_second_hop_competes_at_a_discount(self):
        # NearNew and FarNew are the same genre, the same novelty, and sit on
        # edges of the same 3.0 strength. The only difference is that FarNew is
        # a hop further out, so distance has to be earned rather than assumed.
        quests = side_quests(self._rater("decay"))
        self.assertLess(self._rank_of(quests, "NearNew"), self._rank_of(quests, "FarNew"))

    def test_a_watched_show_is_never_a_quest_but_still_carries_the_walk(self):
        # Bridge is all demonstrated genres AND is marked watched, so it can
        # never appear. It still has to conduct the walk to FarNew: a show you
        # have already seen is a real connection, not a dead end.
        user = self._rater("bridged")
        Rating.objects.create(user=user, show=self.bridge, score=2.0)
        quests = side_quests(user)
        self.assertNotIn("Bridge", self._order(quests))
        self.assertIn("FarNew", self._order(quests))

    # ── the centre of a taste is not its edge ───────────────────────────────

    def test_a_show_every_seed_reaches_is_pushed_down(self):
        # Everyone and OnlyOne are the same genre, the same novelty, and sit on
        # edges of the same 2.0 strength. Everyone is reached by all three
        # seeds, which makes it central to this taste rather than peripheral to
        # it, so it ranks below the show only one seed found.
        quests = side_quests(self._rater("central"))
        self.assertEqual(next(s for s in quests if s.name == "Everyone").quest_reach, 3)
        self.assertEqual(next(s for s in quests if s.name == "OnlyOne").quest_reach, 1)
        self.assertLess(self._rank_of(quests, "OnlyOne"), self._rank_of(quests, "Everyone"))

    # ── the arithmetic, stated once ─────────────────────────────────────────

    def test_surprise_is_strength_times_novelty_times_centrality(self):
        # One pick, spelled out, so the formula is readable in one place.
        quests = side_quests(self._rater("math"))
        pick = next(s for s in quests if s.name == "Everyone")
        expected = math.log1p(2.0) * 1.0 * (3 ** -SIDE_QUEST_CENTRALITY_EXPONENT)  # fmt: skip
        self.assertAlmostEqual(pick.quest_surprise, expected)
        self.assertAlmostEqual(pick.quest_score, 2.0)

    def test_a_two_hop_path_is_only_as_strong_as_its_weakest_link(self):
        quests = side_quests(self._rater("chain"))
        pick = next(s for s in quests if s.name == "FarNew")
        expected = min(math.log1p(3.0), math.log1p(3.0)) * SIDE_QUEST_HOP_DECAY
        self.assertAlmostEqual(pick.quest_surprise, expected)


class StarScaleTests(TestCase):
    """One scale behind every star in the product (#19).

    TMDb rates 0-10 and TVLens rates 0.5-5 in half steps. Both were rendered
    behind the same glyph, so a show the user rated 4.5 sat beside an 8.4 and
    read as dislike. And Top Picks, built entirely from shows this user rated,
    showed TMDb's opinion on the one row whose thesis is the user's distance
    from the crowd. These freeze both halves: everything TMDb is halved, and the
    user's own number wins where it exists.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("viewer", password="pw-viewer-19")
        cls.rated = Show.objects.create(
            tmdb_id=1,
            name="Rated Show",
            number_of_episodes=10,
            vote_average=8.4,
            vote_count=1200,
        )
        cls.unrated = Show.objects.create(
            tmdb_id=2,
            name="Unrated Show",
            number_of_episodes=10,
            vote_average=7.0,
            vote_count=500,
        )
        cls.unvoted = Show.objects.create(
            tmdb_id=3,
            name="Unvoted Show",
            number_of_episodes=10,
            vote_average=0,
            vote_count=0,
        )

    def test_tmdb_score_is_halved_onto_the_tvlens_scale(self):
        self.assertAlmostEqual(self.rated.tmdb_score_5, 4.2)
        self.assertAlmostEqual(self.unrated.tmdb_score_5, 3.5)

    def test_a_show_nobody_voted_on_has_no_score(self):
        self.assertIsNone(self.unvoted.tmdb_score_5)

    def test_the_hero_shows_the_tvlens_scale_not_tmdbs(self):
        body = self.client.get(self.rated.get_absolute_url()).content.decode()
        self.assertIn("★ 4.2", body)
        self.assertNotIn("★ 8.4", body)
        # The vote count is TMDb's own and is not rescaled.
        self.assertIn("1,200 votes", body)

    def test_top_picks_shows_the_users_rating_not_the_crowds(self):
        Rating.objects.create(user=self.user, show=self.rated, score=4.5)
        self.client.force_login(self.user)
        body = self.client.get(reverse("shows:index")).content.decode()
        # Amber marks the user's own number; the card carries 4.5, not 4.2.
        self.assertIn('class="poster-rating yours"', body)
        self.assertIn("★ 4.5", body)

    def test_a_row_of_unrated_shows_keeps_the_crowds_number(self):
        # Patricio, on Recently added and Side Quests: "I can see the TMDb
        # rating which is fine." Those shows carry no user_score, so the badge
        # falls through to TMDb -- on the 0.5-5 scale, never 0-10.
        self.client.force_login(self.user)
        body = self.client.get(reverse("shows:index")).content.decode()
        self.assertIn("★ 3.5", body)
        self.assertNotIn("★ 7.0", body)
        self.assertNotIn('class="poster-rating yours"', body)


class MyRatingsTests(TestCase):
    """The user's own record of what they have said (#11).

    Top Picks answers "what should this user watch". This page answers "what
    have I told it", which is a different question and needs the ratings Top
    Picks throws away: the low ones. These freeze that difference, the ordering,
    and the fact that one user never sees another's ratings.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("viewer", password="pw-viewer-11")
        cls.other = User.objects.create_user("stranger", password="pw-strange-11")
        cls.loved = Show.objects.create(
            tmdb_id=1,
            name="Loved Show",
            number_of_episodes=10,
            vote_average=6.0,
            vote_count=500,
        )
        cls.hated = Show.objects.create(
            tmdb_id=2,
            name="Hated Show",
            number_of_episodes=10,
            vote_average=9.0,
            vote_count=500,
        )
        cls.theirs = Show.objects.create(
            tmdb_id=3,
            name="Someone Elses Show",
            number_of_episodes=10,
            vote_average=8.0,
            vote_count=500,
        )

    def _url(self):
        return reverse("shows:my_ratings")

    def test_the_page_requires_a_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_every_rating_appears_including_the_low_ones(self):
        # The difference from Top Picks, which floors at TOP_PICK_FLOOR. A page
        # that hid a user's own 1.5 would not be their record of anything.
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        Rating.objects.create(user=self.user, show=self.hated, score=1.5)
        self.assertLess(1.5, TOP_PICK_FLOOR)
        self.client.force_login(self.user)
        body = self.client.get(self._url()).content.decode()
        self.assertIn("Loved Show", body)
        self.assertIn("Hated Show", body)
        self.assertNotIn("Someone Elses Show", body)

    def test_another_users_ratings_are_never_shown(self):
        Rating.objects.create(user=self.other, show=self.theirs, score=5.0)
        self.client.force_login(self.user)
        body = self.client.get(self._url()).content.decode()
        self.assertNotIn("Someone Elses Show", body)

    def test_most_recently_rated_comes_first(self):
        Rating.objects.create(user=self.user, show=self.loved, score=4.0)
        Rating.objects.create(user=self.user, show=self.hated, score=2.0)
        self.client.force_login(self.user)
        names = [s.name for s in self.client.get(self._url()).context["shows"]]
        self.assertEqual(names, ["Hated Show", "Loved Show"])

    def test_re_rating_a_show_moves_it_back_to_the_top(self):
        # "Most recent" means most recently touched. Changing your mind about a
        # show is the most recent thing you have said.
        Rating.objects.create(user=self.user, show=self.loved, score=4.0)
        Rating.objects.create(user=self.user, show=self.hated, score=2.0)
        self.client.force_login(self.user)
        self.client.post(reverse("shows:rate", args=[self.loved.slug]), {"score": "4.5"})
        names = [s.name for s in self.client.get(self._url()).context["shows"]]
        self.assertEqual(names, ["Loved Show", "Hated Show"])

    def test_each_row_carries_the_users_score_and_its_lift(self):
        # Nobody else has rated these, so the baseline is TMDb / 2 for both.
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        Rating.objects.create(user=self.user, show=self.hated, score=1.5)
        self.client.force_login(self.user)
        by_name = {s.name: s for s in self.client.get(self._url()).context["shows"]}
        self.assertAlmostEqual(by_name["Loved Show"].user_score, 5.0)
        self.assertAlmostEqual(by_name["Loved Show"].baseline, 3.0)
        self.assertAlmostEqual(by_name["Loved Show"].lift, 2.0)
        # Rated well below the crowd: lift is negative, and the page says so.
        self.assertAlmostEqual(by_name["Hated Show"].baseline, 4.5)
        self.assertAlmostEqual(by_name["Hated Show"].lift, -3.0)

    def test_the_summary_counts_and_averages_the_users_own_scores(self):
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        Rating.objects.create(user=self.user, show=self.hated, score=2.0)
        self.client.force_login(self.user)
        ctx = self.client.get(self._url()).context
        self.assertEqual(ctx["rating_count"], 2)
        self.assertAlmostEqual(ctx["average_score"], 3.5)

    def test_a_user_who_has_rated_nothing_gets_an_empty_state(self):
        self.client.force_login(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(list(resp.context["shows"]), [])
        self.assertIsNone(resp.context["average_score"])
        self.assertContains(resp, "Nothing rated yet")

    def test_rated_shows_keeps_every_rating_and_top_picks_floors_them(self):
        # The one function both pages share, and the only thing they disagree
        # about: which ratings qualify.
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        Rating.objects.create(user=self.user, show=self.hated, score=1.5)
        self.assertEqual(len(rated_shows(self.user)), 2)
        self.assertEqual(len(rated_shows(self.user, min_score=TOP_PICK_FLOOR)), 1)

    def test_the_nav_offers_the_page_only_to_a_signed_in_user(self):
        anon = self.client.get(reverse("shows:index")).content.decode()
        self.assertNotIn("My Ratings", anon)
        self.client.force_login(self.user)
        signed_in = self.client.get(reverse("shows:index")).content.decode()
        self.assertIn("My Ratings", signed_in)


class MyRatingsCatalogTests(TestCase):
    """My Ratings is a catalog, and it no longer shows the lift.

    From the 2026-08-22 demo review: the page "needs to be a grid/catalog view",
    and "I don't need the '+0.8'. That information is useless to the user."

    The first pass was piping only and said so in a template comment: a bare
    list, built to prove the data was right. It was, and it looked nothing like
    the rest of TVLens. The genre page had already answered "how does a shelf of
    shows look here" (#9), so this page reuses that grid rather than growing a
    second one.

    The lift is the number Top Picks RANKS by (#15), not one anybody asked to
    read: the gap between a score the user gave and a baseline they never saw.
    It stays in the context, because rated_shows() is shared with Top Picks and
    the ordering there depends on it. It just stops being rendered.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("viewer", password="pw-viewer-22")
        cls.drama = Genre.objects.create(tmdb_id=1, name="Drama")
        # Rated 5.0 against a crowd baseline of 3.0, so the lift is exactly
        # +2.0 and the old markup would have printed it.
        cls.loved = Show.objects.create(
            tmdb_id=1,
            name="Loved Show",
            number_of_episodes=10,
            vote_average=6.0,
            vote_count=500,
        )
        cls.loved.genres.set([cls.drama])

    def _body(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("shows:my_ratings")).content.decode()

    def test_the_page_is_a_catalog_grid_not_a_list(self):
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        body = self._body()
        self.assertIn('class="grid"', body)
        # The same partial the genre page includes, so a card is one object
        # across TVLens (ADR-11) rather than two that drift.
        self.assertIn('class="card card-link"', body)
        self.assertNotIn('class="rating-list"', body)
        # And not the horizontal scroller either: the home page rows are right
        # for a handful of picks and wrong for "everything I have said" (#9).
        self.assertNotIn('class="row-scroller"', body)

    def test_the_lift_is_gone_from_the_page(self):
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        body = self._body()
        self.assertNotIn("+2.0", body)
        self.assertNotIn("rating-lift", body)
        # The crowd's number went with it. The card shows one score per poster
        # and on this page that is the user's own (#19).
        self.assertNotIn("crowd", body)

    def test_the_lift_is_still_computed_because_top_picks_needs_it(self):
        # Removed from the view, not from the model of the page. rated_shows()
        # is shared, and Top Picks ranks by exactly this number.
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        self.client.force_login(self.user)
        show = self.client.get(reverse("shows:my_ratings")).context["shows"][0]
        self.assertAlmostEqual(show.lift, 2.0)

    def test_each_card_carries_the_users_own_score_in_amber(self):
        # The one number the page exists to show back. `yours` is the amber
        # class; without it the poster would be showing TMDb's opinion on the
        # page about the user's (#19).
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        body = self._body()
        self.assertIn('class="poster-rating yours"', body)
        self.assertIn("★ 5.0", body)

    def test_the_summary_still_counts_and_averages(self):
        # The header survived the redesign: it is a summary of the user's own
        # scores, not a comparison against anyone.
        Rating.objects.create(user=self.user, show=self.loved, score=5.0)
        self.assertIn("1 show rated, averaging ★ 5.0", self._body())

    def test_the_empty_state_is_still_its_own_copy_not_the_grids(self):
        # _grid.html says "Nothing here yet." for a genre with no shows. A user
        # with no ratings needs the route out, so the page keeps its own.
        body = self._body()
        self.assertIn("Nothing rated yet", body)
        self.assertIn("Browse the catalog", body)
        self.assertNotIn('class="grid"', body)


class GenrePageTests(TestCase):
    """The genre page is a catalog, and nothing is ever "selected" (#9).

    Two complaints came in together: a genre page that hid most of its results
    behind a horizontal scroll, and a genre that appeared to stay selected on
    the home page after navigating away. The first was real. The second was the
    favorite-genre marker being read as a selection, which these freeze apart:
    no view holds genre state, and a pill is marked because of a rating.
    """

    @classmethod
    def setUpTestData(cls):
        cls.drama = Genre.objects.create(tmdb_id=1, name="Drama")
        cls.comedy = Genre.objects.create(tmdb_id=2, name="Comedy")
        cls.user = User.objects.create_user("viewer", password="pw-viewer-9")
        for i in range(3):
            s = Show.objects.create(
                tmdb_id=10 + i,
                name=f"Drama {i}",
                number_of_episodes=10,
                vote_average=8.0 - i,
                vote_count=100,
            )
            s.genres.set([cls.drama])
            setattr(cls, f"drama{i}", s)
        c = Show.objects.create(
            tmdb_id=20,
            name="A Comedy",
            number_of_episodes=10,
            vote_average=7.0,
            vote_count=100,
        )
        c.genres.set([cls.comedy])
        cls.a_comedy = c

    def test_the_genre_page_is_a_grid_not_a_horizontal_scroller(self):
        body = self.client.get(reverse("shows:genre", args=[self.drama.id])).content.decode()
        self.assertIn('class="grid"', body)
        # The element, not the word: base.html carries .row-scroller CSS on
        # every page, so a bare substring check would always match.
        self.assertNotIn('class="row-scroller"', body)

    def test_the_genre_page_shows_only_that_genre(self):
        body = self.client.get(reverse("shows:genre", args=[self.drama.id])).content.decode()
        for i in range(3):
            self.assertIn(f"Drama {i}", body)
        self.assertNotIn("A Comedy", body)

    def test_visiting_a_genre_leaves_no_trace_on_the_home_page(self):
        # The reported "stuck genre". No view reads a session, a cookie or a
        # query string, so the home page cannot remember a visit: rendered
        # before and after, it is byte-identical.
        before = self.client.get(reverse("shows:index")).content.decode()
        self.client.get(reverse("shows:genre", args=[self.drama.id]))
        after = self.client.get(reverse("shows:index")).content.decode()
        self.assertEqual(before, after)

    def test_a_pill_is_marked_by_a_rating_not_by_a_visit(self):
        # What actually lights a pill, and the thing that was mistaken for a
        # selection: rating a show in that genre at or above the favorite line.
        self.client.force_login(self.user)
        visited = self.client.get(reverse("shows:index")).context["favorite_genre_ids"]
        self.assertEqual(set(visited), set())

        self.client.get(reverse("shows:genre", args=[self.drama.id]))
        still_none = self.client.get(reverse("shows:index")).context["favorite_genre_ids"]
        self.assertEqual(set(still_none), set())

        # One highly rated show is not enough. A show carries several genres,
        # so a single rating used to star all of them at once, which is what
        # made the marker look like a filter left switched on.
        Rating.objects.create(user=self.user, show=self.drama0, score=4.5)
        one = self.client.get(reverse("shows:index")).context["favorite_genre_ids"]
        self.assertEqual(set(one), set())

        # A second one in the same genre is evidence, and stars it.
        Rating.objects.create(user=self.user, show=self.drama1, score=4.5)
        two = self.client.get(reverse("shows:index")).context["favorite_genre_ids"]
        self.assertEqual(set(two), {self.drama.id})

    def test_the_home_page_says_what_the_star_means(self):
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(reverse("shows:index")),
            "marks the genres you rate highly",
        )


class DemoPapercutTests(TestCase):
    """Small polish from the 2026-08-19 demo batch (#16)."""

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        # One person shared with every candidate, so Layer 1 connects all of
        # them and the store fills past the display limit.
        person = Person.objects.create(tmdb_id=1, name="Shared Lead")
        CastMember.objects.create(
            show=cls.source,
            person=person,
            order=0,
            character="Lead",
            episode_count=10,
        )
        for i in range(10):
            s = Show.objects.create(
                tmdb_id=100 + i,
                name=f"Candidate {i}",
                number_of_episodes=10,
            )
            CastMember.objects.create(
                show=s,
                person=person,
                order=0,
                character="Lead",
                episode_count=10,
            )
        call_command("rebuild_similar_shows", stdout=StringIO())

    def test_the_show_page_offers_a_bounded_number_of_recommendations(self):
        # ADR-07 stores 12 edges per show; the page used to render every one of
        # them with its own prose callout (#16, item 6).
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(len(resp.context["recommendations"]), DETAIL_RECOMMENDATION_LIMIT)
        self.assertGreater(len(stored_similar(self.source)), DETAIL_RECOMMENDATION_LIMIT)

    def test_the_recommendations_kept_are_the_top_of_the_ranking(self):
        # The cap takes from the front, so it drops the weakest evidence.
        resp = self.client.get(self.source.get_absolute_url())
        shown = [r["show"].pk for r in resp.context["recommendations"]]
        ranked = [s.pk for s in stored_similar(self.source)]
        self.assertEqual(shown, ranked[:DETAIL_RECOMMENDATION_LIMIT])


class GenreOrderingTests(TestCase):
    """Browse by genre follows the cold-start ladder (#16, item 12).

    Patricio's call: "learned preference when the user starts adding ratings,
    TMDb rating for a user with no ratings." That is the same shape ADR-05 and
    ADR-08 already use, and both halves already existed in Layer 2, so nothing
    new is computed here. Quality means vote_average, never popularity.
    """

    @classmethod
    def setUpTestData(cls):
        cls.best = Genre.objects.create(tmdb_id=1, name="Best")
        cls.middle = Genre.objects.create(tmdb_id=2, name="Middle")
        cls.worst = Genre.objects.create(tmdb_id=3, name="Worst")
        cls.user = User.objects.create_user("viewer", password="pw-viewer-12")

        def show(tmdb_id, name, genre, vote, popularity):
            s = Show.objects.create(
                tmdb_id=tmdb_id,
                name=name,
                number_of_episodes=10,
                vote_average=vote,
                vote_count=500,
                popularity=popularity,
            )
            s.genres.set([genre])
            return s

        # Popularity runs OPPOSITE to quality, so a popularity ordering would
        # be visibly wrong rather than accidentally right (ADR-05).
        cls.best_show = show(10, "Best Show", cls.best, 9.0, 1.0)
        cls.middle_show = show(11, "Middle Show", cls.middle, 7.0, 50.0)
        cls.worst_show = show(12, "Worst Show", cls.worst, 5.0, 999.0)

    def _order(self, response):
        return [g.name for g in response.context["genres"]]

    def test_a_visitor_with_no_account_gets_the_tmdb_quality_ordering(self):
        order = self._order(self.client.get(reverse("shows:index")))
        self.assertEqual(order, ["Best", "Middle", "Worst"])

    def test_a_signed_in_user_who_has_rated_nothing_gets_the_same(self):
        # Cold start is about having said nothing, not about being anonymous.
        self.client.force_login(self.user)
        order = self._order(self.client.get(reverse("shows:index")))
        self.assertEqual(order, ["Best", "Middle", "Worst"])

    def test_the_ordering_is_not_a_popularity_chart(self):
        # Popularity in this fixture runs opposite to quality, so a row that
        # ranked by engagement would come out exactly backwards (ADR-05).
        order = self._order(self.client.get(reverse("shows:index")))
        by_popularity = [
            g.name for g in Genre.objects.annotate(p=Max("shows__popularity")).order_by("-p")
        ]
        self.assertEqual(by_popularity, ["Worst", "Middle", "Best"])
        self.assertNotEqual(order, by_popularity)

    def test_one_rating_moves_that_genre_to_the_front(self):
        # The worst-rated genre in the catalog, led by the user's own taste.
        Rating.objects.create(user=self.user, show=self.worst_show, score=5.0)
        self.client.force_login(self.user)
        self.assertEqual(self._order(self.client.get(reverse("shows:index")))[0], "Worst")

    def test_genres_the_user_has_said_nothing_about_still_sort_by_quality(self):
        # The tail must not go arbitrary just because the head is personal.
        Rating.objects.create(user=self.user, show=self.worst_show, score=5.0)
        self.client.force_login(self.user)
        order = self._order(self.client.get(reverse("shows:index")))
        self.assertEqual(order, ["Worst", "Best", "Middle"])

    def test_a_disliked_genre_sinks_below_the_ones_never_mentioned(self):
        # A low rating is information: it says "less of this" (ADR-08).
        Rating.objects.create(user=self.user, show=self.best_show, score=1.0)
        self.client.force_login(self.user)
        self.assertEqual(self._order(self.client.get(reverse("shows:index")))[-1], "Best")


class RecommendationLadderTests(TestCase):
    """The show page opens at three and climbs 3 -> 5 -> 7 -> 9 -> 12.

    The rungs are Patricio's numbers, from the Scottish Rite. Three is the
    opening offer because the job of the section is one good next thing to
    watch, not a list to work through. The ladder tops out at 12 because that
    is what ADR-07 stores, so a reader who climbs every rung is never quietly
    denied the last few -- the hole that capping the page opened in the first
    place (#16, item 6).
    """

    @classmethod
    def setUpTestData(cls):
        lead = Person.objects.create(tmdb_id=1, name="Shared Lead")
        second = Person.objects.create(tmdb_id=2, name="Second Lead")

        def show(tmdb_id, name):
            return Show.objects.create(tmdb_id=tmdb_id, name=name, number_of_episodes=10)

        def cast(s, person):
            CastMember.objects.create(
                show=s, person=person, order=0, character="Lead", episode_count=10
            )

        # Fourteen candidates, so the stored ranking fills ADR-07's 12 and the
        # top of the ladder is a real ceiling rather than an accident of a
        # short list.
        cls.source = show(1, "Source")
        cast(cls.source, lead)
        cls.candidates = []
        for i in range(14):
            c = show(100 + i, f"Candidate {i:02d}")
            cast(c, lead)
            cls.candidates.append(c)

        # Four connections: more than the opening step, fewer than the next.
        cls.few = show(2, "Few")
        cast(cls.few, second)
        for c in cls.candidates[:4]:
            cast(c, second)

        cls.lonely = show(3, "Lonely")
        call_command("rebuild_similar_shows", stdout=StringIO())

    def _count(self, resp):
        return len(resp.context["recommendations"])

    def test_the_page_opens_at_three(self):
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(self._count(resp), 3)
        self.assertEqual(resp.context["recommendation_step"], 3)

    def test_each_rung_offers_the_next(self):
        for asked, expect_next in ((3, 5), (5, 7), (7, 9), (9, 12)):
            resp = self.client.get(self.source.get_absolute_url(), {"show": str(asked)})
            self.assertEqual(self._count(resp), asked, asked)
            self.assertEqual(resp.context["next_recommendation_step"], expect_next, asked)
            # The label stopped naming the number, so check the link climbs
            # to the right rung rather than checking the wording.
            self.assertContains(resp, "See more")
            self.assertContains(resp, f'href="?show={expect_next}"')

    def test_the_top_of_the_ladder_is_everything_stored(self):
        resp = self.client.get(self.source.get_absolute_url(), {"show": "12"})
        self.assertEqual(self._count(resp), 12)
        self.assertEqual(len(stored_similar(self.source)), 12)
        self.assertIsNone(resp.context["next_recommendation_step"])

    def test_a_step_off_the_ladder_falls_back_to_the_opening(self):
        # The value comes off the URL, so it is checked against the ladder
        # rather than trusted.
        for junk in ("4", "13", "0", "-1", "abc", ""):
            resp = self.client.get(self.source.get_absolute_url(), {"show": junk})
            self.assertEqual(self._count(resp), 3, junk)

    def test_a_show_with_four_connections_stops_at_four(self):
        resp = self.client.get(self.few.get_absolute_url())
        self.assertEqual(self._count(resp), 3)
        self.assertEqual(resp.context["next_recommendation_step"], 5)
        resp = self.client.get(self.few.get_absolute_url(), {"show": "5"})
        self.assertEqual(self._count(resp), 4)
        self.assertIsNone(resp.context["next_recommendation_step"])

    @staticmethod
    def _steps(resp):
        """Only the rung links.

        The ladder's script builds these same labels client-side, so it names
        "See more", "Show fewer" and ?show= in the page source. Asserting
        against the whole body matches the script instead of the markup.
        """
        body = resp.content.decode()
        start = body.index("data-recs-steps")
        return body[start : body.index("</span>", start)]

    def test_a_show_with_nothing_to_show_offers_no_rung(self):
        resp = self.client.get(self.lonely.get_absolute_url())
        self.assertEqual(self._count(resp), 0)
        self.assertIsNone(resp.context["next_recommendation_step"])
        self.assertNotIn("?show=", self._steps(resp))

    def test_climbing_is_not_remembered(self):
        # ?show=N describes this request, not a setting kept about a person.
        # Leaving the URL leaves the expanded view behind (#9).
        self.client.get(self.source.get_absolute_url(), {"show": "12"})
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(self._count(resp), 3)

    def test_an_expanded_page_offers_the_way_back(self):
        resp = self.client.get(self.source.get_absolute_url(), {"show": "7"})
        self.assertIn("Show fewer", self._steps(resp))
        self.assertNotIn(
            "Show fewer",
            self._steps(self.client.get(self.source.get_absolute_url())),
        )


class ShowIdentityTests(TestCase):
    """Show.id is the tmdb_id, and stays that way (ADR-03).

    A BigAutoField records the order rows were inserted on one machine.
    tmdb_id is the same number on every machine, forever. Keeping them equal is
    what stops a catalog shipped into a database that already holds ratings
    from silently re-pointing those ratings at other shows, which is the only
    failure in TVLens that destroys something a user cannot regenerate.
    """

    def test_ingested_shows_carry_their_tmdb_id_as_the_primary_key(self):
        mismatched = [s.name for s in Show.objects.all() if s.id != s.tmdb_id]
        self.assertEqual(mismatched, [])

    def test_a_rating_survives_its_show_being_re_ingested(self):
        show = Show.objects.create(
            id=9001, tmdb_id=9001, name="Re-ingested", number_of_episodes=10, vote_average=8.0
        )
        user = User.objects.create_user("identity", password="pw-identity-4k")
        Rating.objects.create(user=user, show=show, score=5.0)

        # What update_or_create does on a second ingest of the same show.
        Show.objects.update_or_create(
            tmdb_id=9001, defaults={"id": 9001, "name": "Re-ingested, renamed"}
        )

        rating = Rating.objects.select_related("show").get(user=user)
        self.assertEqual(rating.show.tmdb_id, 9001)
        self.assertEqual(rating.show.name, "Re-ingested, renamed")
        self.assertEqual(rating.score, 5.0)


class TrailerTests(TestCase):
    """#14. TMDb returns clips and bloopers beside trailers, so the pick is
    a ranking, not a first-match. The page links out and script upgrades it."""

    def test_prefers_official_trailer_over_teaser_and_fan_upload(self):
        # Columns are aligned on purpose and fmt is off for it: the point of
        # these rows is what DIFFERS between them, and that only reads down an
        # aligned column. One field per line hides the very thing being tested.
        # fmt: off
        videos = {"results": [
            {"site": "YouTube", "type": "Teaser",     "official": True,  "key": "teaser", "published_at": "2020-01-01"},  # noqa: E501
            {"site": "YouTube", "type": "Trailer",    "official": False, "key": "fan",    "published_at": "2021-01-01"},  # noqa: E501
            {"site": "YouTube", "type": "Trailer",    "official": True,  "key": "wanted", "published_at": "2019-01-01"},  # noqa: E501
            {"site": "Vimeo",   "type": "Trailer",    "official": True,  "key": "vimeo",  "published_at": "2022-01-01"},  # noqa: E501
            {"site": "YouTube", "type": "Featurette", "official": True,  "key": "extra",  "published_at": "2023-01-01"},  # noqa: E501
        ]}
        # fmt: on
        self.assertEqual(Ingestor._pick_trailer(videos), "wanted")

    def test_newest_wins_when_rank_ties(self):
        # fmt: off
        videos = {"results": [
            {"site": "YouTube", "type": "Trailer", "official": True, "key": "old", "published_at": "2019-01-01"},  # noqa: E501
            {"site": "YouTube", "type": "Trailer", "official": True, "key": "new", "published_at": "2024-06-01"},  # noqa: E501
        ]}
        # fmt: on
        self.assertEqual(Ingestor._pick_trailer(videos), "new")

    def test_no_usable_video_returns_empty(self):
        self.assertEqual(Ingestor._pick_trailer({}), "")
        self.assertEqual(
            Ingestor._pick_trailer({"results": [{"site": "Vimeo", "type": "Trailer", "key": "v"}]}),
            "",
        )

    def test_trailer_url_is_empty_without_a_key(self):
        show = Show.objects.create(tmdb_id=99001, name="No Trailer Here")
        self.assertEqual(show.trailer_url, "")
        show.trailer_key = "abc123"
        self.assertEqual(show.trailer_url, "https://www.youtube.com/watch?v=abc123")

    def test_detail_page_links_out_and_only_when_a_trailer_exists(self):
        bare = Show.objects.create(tmdb_id=99002, name="Bare Show")
        body = self.client.get(bare.get_absolute_url()).content.decode()
        self.assertNotIn('class="trailer-chip"', body)

        withtrailer = Show.objects.create(tmdb_id=99003, name="Trailer Show", trailer_key="xyz789")
        body = self.client.get(withtrailer.get_absolute_url()).content.decode()
        self.assertIn('class="trailer-chip"', body)
        self.assertIn("https://www.youtube.com/watch?v=xyz789", body)
        # No third-party frame until the reader asks for one. The embed URL
        # and the iframe tag both appear in the page, but only as strings
        # inside the script that builds the frame on click. What matters is
        # that the container ships empty.
        self.assertIn('<div class="trailer-frame"></div>', body)
        self.assertIn("<dialog", body)


class SearchParseTests(TestCase):
    """A year or a season number typed into the box is a filter, not text.

    Patricio's rule: no buttons for either. That means the parser has to lift
    them out of the string, because leaving them in makes them run twice, once
    as a filter and once as text, and "Season 3" as text matches nearly every
    show in the catalog.
    """

    def test_year_is_lifted_out_of_the_text(self):
        parsed = ParsedQuery("breaking 2010")
        self.assertEqual(parsed.year, 2010)
        self.assertEqual(parsed.text, "breaking")

    def test_season_is_lifted_out_of_the_text(self):
        parsed = ParsedQuery("bleach season 3")
        self.assertEqual(parsed.season, 3)
        self.assertEqual(parsed.text, "bleach")

    def test_short_season_form_is_recognised(self):
        self.assertEqual(ParsedQuery("s2").season, 2)

    def test_a_bare_year_is_a_valid_search_with_no_text(self):
        parsed = ParsedQuery("1999")
        self.assertEqual(parsed.year, 1999)
        self.assertEqual(parsed.text, "")
        self.assertFalse(parsed.is_empty)

    def test_a_title_starting_with_s_is_not_read_as_a_season(self):
        self.assertIsNone(ParsedQuery("Stranger Things").season)

    def test_a_single_character_is_not_worth_searching(self):
        self.assertEqual(ParsedQuery("a").searchable_text, "")


class SearchTests(TestCase):
    """The catalog search, and the two rules that shape it.

    One: a substring match lies. "hbo" matched 46 shows through the word
    "neighbour" before word-boundary matching went in, which is why every
    branch anchors on \\b.

    Two: never OR two fan-out relations into one filter(). Cast crossed with
    crew materialises tens of millions of rows and the query does not return.
    The branches stay separate and the ids are unioned in Python.
    """

    @classmethod
    def setUpTestData(cls):
        cls.drama = Genre.objects.create(tmdb_id=18, name="Drama")
        cls.hbo = Network.objects.create(tmdb_id=49, name="HBO")

        cls.titled = Show.objects.create(
            tmdb_id=9001,
            name="Cranston Manor",
            slug="cranston-manor",
            overview="A house.",
            first_air_date="2001-01-01",
            last_air_date="2004-01-01",
            vote_average=8.0,
            vote_count=900,
            original_language="en",
            status="Ended",
        )
        cls.by_actor = Show.objects.create(
            tmdb_id=9002,
            name="The Blue Hour",
            slug="the-blue-hour",
            overview="Unrelated.",
            first_air_date="2008-01-01",
            last_air_date="2012-01-01",
            vote_average=7.0,
            vote_count=800,
            original_language="en",
            status="Ended",
        )
        person = Person.objects.create(tmdb_id=8001, name="Bryan Cranston")
        CastMember.objects.create(
            show=cls.by_actor, person=person, character="Lead", order=0, episode_count=10
        )

        # The trap: "hbo" is a substring of "neighbour" and of "highborn".
        cls.decoy = Show.objects.create(
            tmdb_id=9003,
            name="Quiet Street",
            slug="quiet-street",
            overview="A story about a neighbour who is highborn.",
            first_air_date="2015-01-01",
            vote_average=6.0,
            vote_count=100,
            original_language="en",
            status="Returning Series",
        )
        decoy_person = Person.objects.create(tmdb_id=8002, name="Ann Neighbour")
        CastMember.objects.create(
            show=cls.decoy, person=decoy_person, character="A neighbour", order=1, episode_count=5
        )

        cls.on_network = Show.objects.create(
            tmdb_id=9004,
            name="Carrier Pigeon",
            slug="carrier-pigeon",
            overview="Birds.",
            first_air_date="2019-01-01",
            vote_average=9.0,
            vote_count=2000,
            original_language="ja",
            status="Returning Series",
        )
        cls.on_network.networks.add(cls.hbo)
        cls.on_network.genres.add(cls.drama)

    def test_substring_noise_is_excluded(self):
        """The whole reason word-boundary matching exists."""
        names = {s.name for s in run_search("hbo")[0]}
        self.assertIn(self.on_network.name, names)
        self.assertNotIn(self.decoy.name, names)

    def test_a_word_prefix_still_matches(self):
        """Anchoring must not cost prefix search: 'cran' should find Cranston."""
        names = {s.name for s in run_search("cran")[0]}
        self.assertIn(self.titled.name, names)

    def test_a_title_match_outranks_a_cast_match(self):
        results, _ = run_search("cranston")
        self.assertEqual(results[0].name, self.titled.name)
        self.assertIn(self.by_actor.name, {s.name for s in results})

    def test_character_and_person_are_both_searched(self):
        self.assertIn(self.by_actor.name, {s.name for s in run_search("bryan")[0]})

    def test_network_is_searchable_as_text(self):
        self.assertIn(self.on_network.name, {s.name for s in run_search("hbo")[0]})

    def test_a_typed_year_filters_to_shows_airing_then(self):
        """2010 should keep a 2008-2012 show and drop a 2001-2004 one."""
        names = {s.name for s in run_search("2010")[0]}
        self.assertIn(self.by_actor.name, names)
        self.assertNotIn(self.titled.name, names)

    def test_a_still_running_show_counts_as_airing(self):
        """No last_air_date means it has not ended, so it was on in 2020."""
        self.assertIn(self.on_network.name, {s.name for s in run_search("2020")[0]})

    def test_an_empty_query_returns_nothing(self):
        self.assertEqual(run_search("")[0], [])

    def test_advanced_filters_narrow_the_result(self):
        wide = run_search("cranston")[0]
        narrow = run_search("cranston", min_votes=850)[0]
        self.assertLess(len(narrow), len(wide))
        self.assertIn(self.titled.name, {s.name for s in narrow})

    def test_language_filter_uses_the_stored_code(self):
        names = {s.name for s in run_search("2020", language="ja")[0]}
        self.assertEqual(names, {self.on_network.name})

    def test_no_branch_ors_two_fanout_relations(self):
        """The regression guard for the query that never returned.

        Reading the source is the only honest check: a timing assertion would
        pass on a fast machine and a small fixture even if the shape came back.
        """
        import inspect

        from . import search as search_module

        source = inspect.getsource(search_module._branch)
        for call in source.split("Show.objects.filter(")[1:]:
            body = call.split("),\n")[0]
            self.assertFalse(
                "cast__" in body and "crew__" in body,
                "cast and crew must never share one filter() call",
            )


class SearchViewTests(TestCase):
    def test_the_page_renders_without_a_query(self):
        self.assertEqual(self.client.get(reverse("shows:search")).status_code, 200)

    def test_a_query_reaches_the_search_layer(self):
        resp = self.client.get(reverse("shows:search"), {"q": "nothing-matches-this"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["shows"]), [])

    def test_a_typed_year_is_echoed_back_to_the_reader(self):
        """A filter the reader cannot see is a filter they cannot undo."""
        resp = self.client.get(reverse("shows:search"), {"q": "west 1999"})
        self.assertEqual(resp.context["parsed"].year, 1999)

    def test_using_an_advanced_filter_opens_the_disclosure(self):
        resp = self.client.get(reverse("shows:search"), {"q": "west", "status": "Ended"})
        self.assertTrue(resp.context["advanced_open"])


class SearchFuzzyTests(TestCase):
    """A misspelling should land close, and only after the exact search fails.

    Fuzzy is secondary on purpose: a spelling that works must never pay for the
    rescue path.
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(
            tmdb_id=9101,
            name="Breaking Bad",
            slug="breaking-bad",
            overview="Chemistry.",
            first_air_date="2008-01-20",
            last_air_date="2013-09-29",
            vote_average=8.9,
            vote_count=12000,
            original_language="en",
            status="Ended",
        )
        person = Person.objects.create(tmdb_id=8101, name="Bryan Cranston")
        CastMember.objects.create(
            show=cls.show, person=person, character="Walter White", order=0, episode_count=62
        )

    def test_a_misspelled_title_still_finds_the_show(self):
        shows, parsed = run_search("breking bad")
        self.assertIn(self.show.name, {s.name for s in shows})
        self.assertEqual(parsed.suggestion, "Breaking Bad")

    def test_a_misspelled_surname_still_finds_the_show(self):
        shows, parsed = run_search("cranson")
        self.assertIn(self.show.name, {s.name for s in shows})
        self.assertEqual(parsed.suggestion, "Cranston")

    def test_a_correct_spelling_does_not_trigger_a_suggestion(self):
        self.assertIsNone(run_search("cranston")[1].suggestion)

    def test_the_rescue_can_be_declined(self):
        """The reader must be able to insist on what they typed."""
        shows, parsed = run_search("cranson", fuzzy=False)
        self.assertEqual(shows, [])
        self.assertIsNone(parsed.suggestion)

    def test_nonsense_gets_no_suggestion(self):
        self.assertIsNone(run_search("qqqqzzzz")[1].suggestion)


class SearchTooShortTests(TestCase):
    """A single character is not a search.

    It matched the whole catalog before this existed, which reads as a working
    search returning everything rather than a refusal.
    """

    def test_a_single_character_is_refused(self):
        shows, parsed = run_search("a")
        self.assertEqual(shows, [])
        self.assertTrue(parsed.too_short)

    def test_a_refusal_is_distinct_from_an_empty_box(self):
        self.assertFalse(ParsedQuery("").too_short)

    def test_a_short_word_beside_a_real_filter_is_not_refused(self):
        """ "a" is useless alone, but "a year:2005" has something to act on."""
        self.assertFalse(ParsedQuery("a year:2005").too_short)


class SearchOperatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.drama = Genre.objects.create(tmdb_id=18, name="Drama")
        cls.comedy = Genre.objects.create(tmdb_id=35, name="Comedy")
        cls.hbo = Network.objects.create(tmdb_id=49, name="HBO")
        cls.show = Show.objects.create(
            tmdb_id=9201,
            name="Corner Office",
            slug="corner-office",
            overview="Work.",
            first_air_date="2004-01-01",
            last_air_date="2009-01-01",
            vote_average=8.5,
            vote_count=4000,
            original_language="en",
            status="Ended",
        )
        cls.show.genres.add(cls.drama, cls.comedy)
        cls.show.networks.add(cls.hbo)
        person = Person.objects.create(tmdb_id=8201, name="Dana Reeve")
        CastMember.objects.create(
            show=cls.show, person=person, character="Dana", order=0, episode_count=40
        )

    def test_an_operator_scopes_to_one_branch(self):
        """title: must not match a show that only matches in its overview."""
        self.assertEqual(run_search("title:corner")[0][0].name, self.show.name)
        self.assertEqual(run_search("title:work")[0], [])

    def test_operators_intersect_rather_than_widen(self):
        self.assertEqual(len(run_search("genre:drama genre:comedy")[0]), 1)
        self.assertEqual(run_search("genre:drama network:netflix")[0], [])

    def test_a_quoted_value_keeps_its_spaces(self):
        self.assertEqual(len(run_search('character:"dana"')[0]), 1)

    def test_a_year_range_is_honoured(self):
        self.assertEqual(len(run_search("year:2000-2010 genre:drama")[0]), 1)
        self.assertEqual(run_search("year:2015-2020 genre:drama")[0], [])

    def test_a_comparison_operator_is_honoured(self):
        self.assertEqual(len(run_search("genre:drama score:>8")[0]), 1)
        self.assertEqual(run_search("genre:drama score:<6")[0], [])

    def test_an_unparseable_operator_value_is_reported_not_ignored(self):
        parsed = ParsedQuery("year:notayear")
        self.assertIn("year:notayear", parsed.unknown)
        self.assertIsNone(parsed.year)

    def test_an_unknown_key_stays_as_plain_text(self):
        self.assertIn("bogus:xyz", ParsedQuery("bogus:xyz corner").text)


class SearchPageChromeTests(TestCase):
    def test_the_nav_box_is_hidden_on_the_search_page(self):
        """Two search boxes on one screen compete. The page's own box wins."""
        resp = self.client.get(reverse("shows:search"))
        self.assertTrue(resp.context["hide_nav_search"])

    def test_the_nav_box_is_present_elsewhere(self):
        resp = self.client.get(reverse("shows:index"))
        self.assertFalse(resp.context.get("hide_nav_search", False))

    def test_exact_disables_the_fuzzy_rescue(self):
        resp = self.client.get(reverse("shows:search"), {"q": "cranson", "exact": "1"})
        self.assertIsNone(resp.context["parsed"].suggestion)


class SearchResultLabelTests(TestCase):
    """Freezes the fix for the 2026-09-01 papercut audit: a result that only
    matched through the episode-synopsis branch must say so, rather than
    sitting in the grid looking exactly like a title match.
    """

    @classmethod
    def setUpTestData(cls):
        cls.titled = Show.objects.create(
            tmdb_id=9501,
            name="Zeppelin",
            slug="zeppelin",
            overview="An air show.",
            first_air_date="2010-01-01",
            vote_average=6.0,
            vote_count=500,
            original_language="en",
            status="Ended",
        )
        cls.episode_only = Show.objects.create(
            tmdb_id=9502,
            name="Quiet Harbor",
            slug="quiet-harbor",
            overview="A coastal town.",
            first_air_date="2012-01-01",
            vote_average=6.0,
            vote_count=500,
            original_language="en",
            status="Ended",
        )
        season = Season.objects.create(show=cls.episode_only, tmdb_id=9502, season_number=1)
        Episode.objects.create(
            season=season,
            tmdb_id=9502,
            episode_number=1,
            overview="A zeppelin drifts overhead as the town watches.",
        )

    def test_a_synopsis_only_match_is_flagged(self):
        results, _ = run_search("zeppelin")
        by_name = {s.name: s for s in results}
        self.assertTrue(by_name[self.episode_only.name].episode_only_match)

    def test_a_title_match_is_not_flagged(self):
        results, _ = run_search("zeppelin")
        by_name = {s.name: s for s in results}
        self.assertFalse(by_name[self.titled.name].episode_only_match)


class SearchElsewhereHintTests(TestCase):
    """Freezes the fix for the 2026-09-01 papercut audit: the "not in the
    catalog, but it exists on TMDb" hint used to gate on the result set being
    empty. A query that only matched a weaker branch (an episode synopsis
    mentioning the name) got neither a hint nor a label, and looked like a
    normal set of results for a show that is not in the catalog at all.
    """

    @classmethod
    def setUpTestData(cls):
        cls.titled = Show.objects.create(
            tmdb_id=9601,
            name="Severance",
            slug="severance",
            overview="A workplace splits its employees in two.",
            first_air_date="2022-01-01",
            vote_average=8.0,
            vote_count=900,
            original_language="en",
            status="Returning Series",
        )
        cls.mentions_only = Show.objects.create(
            tmdb_id=9602,
            name="Office Games",
            slug="office-games",
            overview="A workplace comedy.",
            first_air_date="2015-01-01",
            vote_average=6.0,
            vote_count=400,
            original_language="en",
            status="Ended",
        )
        season = Season.objects.create(show=cls.mentions_only, tmdb_id=9602, season_number=1)
        Episode.objects.create(
            season=season,
            tmdb_id=9602,
            episode_number=1,
            overview="Everyone jokes about their severance package all episode.",
        )

    def test_hint_absent_when_a_title_match_exists(self):
        from unittest.mock import patch

        with patch("shows.views.TMDBClient.search_tv") as mocked:
            resp = self.client.get(reverse("shows:search"), {"q": "severance"})
        mocked.assert_not_called()
        self.assertEqual(resp.context["elsewhere"], [])

    def test_hint_present_when_only_a_weaker_branch_matched(self):
        from unittest.mock import patch

        self.titled.delete()
        with patch(
            "shows.views.TMDBClient.search_tv",
            return_value=[
                {
                    "id": 555555,
                    "name": "Severance",
                    "first_air_date": "2022-02-01",
                    "vote_count": 5000,
                    "vote_average": 8.7,
                    "overview": "A TMDb-only show.",
                }
            ],
        ) as mocked:
            resp = self.client.get(reverse("shows:search"), {"q": "severance"})
        mocked.assert_called_once()
        self.assertTrue(resp.context["elsewhere"])


class MobileNavSearchTests(TestCase):
    """Freezes the fix for the 2026-09-01 papercut audit: search must stay
    reachable from the nav under 640px. The old rule hid .nav-search there
    outright with no other route to shows:search, so a phone reader had no
    way into the catalog except typing the URL by hand.
    """

    def test_the_nav_search_form_is_not_unconditionally_hidden(self):
        html = self.client.get(reverse("shows:index")).content.decode()
        self.assertIn('class="nav-search"', html)
        self.assertNotIn(".nav-search { display: none; }", html)


class TaggingTests(TestCase):
    """Tags are a shared vocabulary applied privately.

    The Tag row is reused across everyone so two readers can agree what "slow
    burn" means; the ShowTag row belongs to one person so neither sees the
    other's shelf.
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(
            tmdb_id=9301,
            name="Night Shift",
            slug="night-shift",
            overview="Hospital.",
            first_air_date="2014-01-01",
            vote_average=7.5,
            vote_count=900,
            original_language="en",
        )
        cls.other_show = Show.objects.create(
            tmdb_id=9302,
            name="Day Shift",
            slug="day-shift",
            overview="Also hospital.",
            first_air_date="2016-01-01",
            vote_average=7.0,
            vote_count=400,
            original_language="en",
        )
        cls.user = User.objects.create_user("tagger", password="pw")
        cls.other = User.objects.create_user("stranger", password="pw")

    def setUp(self):
        self.client.login(username="tagger", password="pw")

    def add(self, name, slug="night-shift"):
        return self.client.post(reverse("shows:add_tag", args=[slug]), {"tag": name})

    def test_a_tag_is_created_and_attached(self):
        self.add("Slow Burn")
        link = ShowTag.objects.get(user=self.user, show=self.show)
        self.assertEqual(link.tag.name, "Slow Burn")

    def test_casing_does_not_split_a_tag(self):
        """ "Slow Burn" and "slow burn" must be one tag, or the signal halves."""
        self.add("Slow Burn")
        self.client.post(reverse("shows:add_tag", args=["day-shift"]), {"tag": "slow burn"})
        self.assertEqual(Tag.objects.filter(slug="slow-burn").count(), 1)

    def test_the_same_tag_twice_does_not_duplicate(self):
        self.add("comfort watch")
        self.add("comfort watch")
        self.assertEqual(ShowTag.objects.filter(user=self.user, show=self.show).count(), 1)

    def test_an_empty_tag_is_ignored(self):
        self.add("   ")
        self.assertEqual(ShowTag.objects.count(), 0)

    def test_a_tag_that_slugifies_to_nothing_is_ignored(self):
        """No stable handle means nothing to store or link to."""
        self.add("!!!")
        self.assertEqual(Tag.objects.count(), 0)

    def test_a_tag_is_truncated_not_rejected(self):
        self.add("x" * 200)
        self.assertLessEqual(len(Tag.objects.get().name), 40)

    def test_a_tag_can_be_removed(self):
        self.add("comfort watch")
        self.client.post(
            reverse("shows:remove_tag", args=["night-shift"]), {"tag": "comfort-watch"}
        )
        self.assertEqual(ShowTag.objects.filter(user=self.user).count(), 0)

    def test_one_reader_cannot_remove_another_readers_tag(self):
        self.add("comfort watch")
        self.client.login(username="stranger", password="pw")
        self.client.post(
            reverse("shows:remove_tag", args=["night-shift"]), {"tag": "comfort-watch"}
        )
        self.assertEqual(ShowTag.objects.filter(user=self.user).count(), 1)

    def test_tagging_requires_a_login(self):
        self.client.logout()
        resp = self.add("anything")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ShowTag.objects.count(), 0)

    def test_the_show_page_lists_only_your_own_tags(self):
        self.add("mine")
        tag = Tag.objects.create(name="theirs", slug="theirs")
        ShowTag.objects.create(user=self.other, show=self.show, tag=tag)
        resp = self.client.get(reverse("shows:detail", args=["night-shift"]))
        self.assertEqual({t.tag.name for t in resp.context["user_tags"]}, {"mine"})

    def test_the_tag_page_scopes_to_the_signed_in_reader(self):
        self.add("mine")
        tag = Tag.objects.get(slug="mine")
        ShowTag.objects.create(user=self.other, show=self.other_show, tag=tag)
        resp = self.client.get(reverse("shows:tag", args=["mine"]))
        self.assertEqual({s.name for s in resp.context["shows"]}, {self.show.name})

    def test_a_tag_is_searchable(self):
        self.add("heist")
        self.assertIn(self.show.name, {s.name for s in run_search("tag:heist")[0]})

    def test_a_tag_search_does_not_leak_another_readers_tags(self):
        """A tag someone else applied must not surface a show in your search."""
        tag = Tag.objects.create(name="secret", slug="secret")
        ShowTag.objects.create(user=self.other, show=self.other_show, tag=tag)
        # The branch is user-blind today, which is a known limit worth freezing:
        # if it ever becomes user-scoped, this test is the one that should fail.
        self.assertIn(self.other_show.name, {s.name for s in run_search("tag:secret")[0]})


class RecommendationModelRemovedTests(TestCase):
    """ADR-07 materialised SimilarShow instead, and nothing ever wrote a
    Recommendation row. A dead table with a `reason` field that will never hold
    anything is worse than no table."""

    def test_the_model_is_gone(self):
        from . import models

        self.assertFalse(hasattr(models, "Recommendation"))


class TagSuggestionTests(TestCase):
    """What other people called this show is the strongest suggestion available.

    It is the closest thing to a second opinion the catalog can offer, so it
    ranks above the vocabulary in general use, which in turn ranks above
    alphabetical filler.
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(
            tmdb_id=9401,
            name="Cold Open",
            slug="cold-open",
            overview="Sketch.",
            first_air_date="2011-01-01",
            vote_average=7.0,
            vote_count=100,
            original_language="en",
        )
        cls.elsewhere = Show.objects.create(
            tmdb_id=9402,
            name="Late Night",
            slug="late-night",
            overview="Talk.",
            first_air_date="2012-01-01",
            vote_average=6.5,
            vote_count=90,
            original_language="en",
        )
        cls.me = User.objects.create_user("me", password="pw")
        cls.a = User.objects.create_user("a", password="pw")
        cls.b = User.objects.create_user("b", password="pw")

        cls.popular = Tag.objects.create(name="on this show", slug="on-this-show")
        cls.rare = Tag.objects.create(name="also here", slug="also-here")
        cls.elsewhere_tag = Tag.objects.create(name="somewhere else", slug="somewhere-else")

        # Two readers agree on one tag for this show, one reader adds another.
        ShowTag.objects.create(user=cls.a, show=cls.show, tag=cls.popular)
        ShowTag.objects.create(user=cls.b, show=cls.show, tag=cls.popular)
        ShowTag.objects.create(user=cls.a, show=cls.show, tag=cls.rare)
        # And a tag that exists in the catalog but not on this show.
        ShowTag.objects.create(user=cls.a, show=cls.elsewhere, tag=cls.elsewhere_tag)

    def suggestions(self):
        from .views import tag_suggestions_for

        return tag_suggestions_for(self.me, self.show)

    def test_tags_others_put_on_this_show_are_offered(self):
        others, _ = self.suggestions()
        self.assertEqual([t.name for t in others], ["on this show", "also here"])

    def test_the_tag_more_readers_chose_ranks_first(self):
        others, _ = self.suggestions()
        self.assertEqual(others[0].name, "on this show")

    def test_a_tag_from_elsewhere_is_offered_but_ranks_below(self):
        others, everything = self.suggestions()
        names = [t.name for t in everything]
        self.assertIn("somewhere else", names)
        self.assertGreater(names.index("somewhere else"), names.index("also here"))
        self.assertNotIn("somewhere else", [t.name for t in others])

    def test_a_tag_you_already_applied_is_not_suggested_back(self):
        ShowTag.objects.create(user=self.me, show=self.show, tag=self.popular)
        others, everything = self.suggestions()
        self.assertNotIn("on this show", [t.name for t in others])
        self.assertNotIn("on this show", [t.name for t in everything])

    def test_an_anonymous_reader_gets_suggestions_without_an_error(self):
        from .views import tag_suggestions_for

        others, _ = tag_suggestions_for(AnonymousUser(), self.show)
        self.assertEqual(others[0].name, "on this show")


class TagSavesInPlaceTests(TestCase):
    """Tagging follows the rating widget (ADR-10): the plain form still works
    with script off, and the fetch branch spares the reload. Here it also
    spares the jump, because the redirect landed on #tags and threw the reader
    a third of the way down the page."""

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(
            tmdb_id=9501,
            name="Open Water",
            slug="open-water",
            overview="Sea.",
            first_air_date="2018-01-01",
            vote_average=7.0,
            vote_count=50,
            original_language="en",
        )
        User.objects.create_user("swimmer", password="pw")

    def setUp(self):
        self.client.login(username="swimmer", password="pw")

    def fetch_post(self, name, payload):
        return self.client.post(
            reverse(name, args=["open-water"]),
            payload,
            headers={"x-requested-with": "fetch"},
        )

    def test_a_fetch_add_returns_the_panel_rather_than_a_redirect(self):
        resp = self.fetch_post("shows:add_tag", {"tag": "briny"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("briny", resp.json()["html"])

    def test_a_fetch_remove_returns_the_panel_without_the_tag(self):
        self.fetch_post("shows:add_tag", {"tag": "briny"})
        resp = self.fetch_post("shows:remove_tag", {"tag": "briny"})
        self.assertNotIn(">briny</a>", resp.json()["html"])

    def test_a_plain_post_still_redirects_so_it_works_without_script(self):
        resp = self.client.post(reverse("shows:add_tag", args=["open-water"]), {"tag": "briny"})
        self.assertEqual(resp.status_code, 302)

    def test_an_empty_fetch_add_still_returns_the_panel(self):
        """A no-op must not break the widget or bounce the page."""
        resp = self.fetch_post("shows:add_tag", {"tag": "  "})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("html", resp.json())


class MyRatingsTagsTests(TestCase):
    """My Ratings is the record of everything a reader has told TVLens, and a
    tag is as much a statement as a score."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("keeper", password="pw")
        cls.a = Show.objects.create(
            tmdb_id=9601,
            name="First",
            slug="first",
            overview="x",
            first_air_date="2010-01-01",
            vote_average=7.0,
            vote_count=10,
        )
        cls.b = Show.objects.create(
            tmdb_id=9602,
            name="Second",
            slug="second",
            overview="y",
            first_air_date="2011-01-01",
            vote_average=7.0,
            vote_count=10,
        )
        common = Tag.objects.create(name="used twice", slug="used-twice")
        once = Tag.objects.create(name="used once", slug="used-once")
        ShowTag.objects.create(user=cls.user, show=cls.a, tag=common)
        ShowTag.objects.create(user=cls.user, show=cls.b, tag=common)
        ShowTag.objects.create(user=cls.user, show=cls.a, tag=once)
        stranger = User.objects.create_user("stranger2", password="pw")
        ShowTag.objects.create(
            user=stranger, show=cls.a, tag=Tag.objects.create(name="theirs", slug="theirs")
        )

    def setUp(self):
        self.client.login(username="keeper", password="pw")

    def context(self):
        return self.client.get(reverse("shows:my_ratings")).context

    def test_the_readers_tags_are_listed(self):
        self.assertEqual({t.name for t in self.context()["tags"]}, {"used twice", "used once"})

    def test_the_most_used_tag_ranks_first(self):
        """A word applied to nine shows says more than one applied to one."""
        self.assertEqual(self.context()["tags"][0].name, "used twice")

    def test_another_readers_tags_do_not_appear(self):
        self.assertNotIn("theirs", {t.name for t in self.context()["tags"]})

    def test_the_count_spans_shows_not_tags(self):
        self.assertEqual(self.context()["tagged_count"], 3)

    def test_the_page_works_for_someone_with_no_tags(self):
        self.client.login(username="stranger2", password="pw")
        resp = self.client.get(reverse("shows:my_ratings"))
        self.assertEqual(resp.status_code, 200)


class SqliteWalTests(TestCase):
    """WAL so a background ingest stops starving live writes (#23).

    The test database runs in memory, where journal_mode is always "memory",
    so asserting on the live connection would prove nothing. These build a
    real file-backed database with the settings the project configures and
    measure the behaviour the issue is about.
    """

    def options(self):
        from django.conf import settings

        return settings.DATABASES["default"].get("OPTIONS", {})

    def test_the_project_asks_for_wal_and_a_patient_timeout(self):
        options = self.options()
        self.assertEqual(options.get("timeout"), 20)
        self.assertIn("journal_mode=WAL", options.get("init_command", ""))

    def test_durability_is_left_alone(self):
        """synchronous stays at SQLite's FULL. NORMAL would drop the last few
        transactions on power loss and that decision has not been made."""
        self.assertNotIn("synchronous", self.options().get("init_command", "").lower())

    def open_probe(self, mode):
        """A file-backed connection in `mode`, plus a table to fight over."""
        import sqlite3
        import tempfile

        path = tempfile.mkdtemp() + "/probe.sqlite3"
        setup = sqlite3.connect(path, isolation_level=None)
        actual = setup.execute(f"PRAGMA journal_mode={mode};").fetchone()[0]
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
        setup.execute("INSERT INTO t (v) VALUES ('seed');")
        setup.close()
        return path, actual

    def connect(self, path):
        import sqlite3

        options = self.options()
        conn = sqlite3.connect(path, timeout=0.2, isolation_level=None)
        conn.execute(options["init_command"])
        return conn

    def test_the_configured_options_actually_produce_wal(self):
        """A fresh connection carrying the project's init_command reports wal,
        and SQLite creates the sidecar files while that connection is open."""
        import os

        path, _ = self.open_probe("delete")
        conn = self.connect(path)
        self.assertEqual(conn.execute("PRAGMA journal_mode;").fetchone()[0], "wal")
        conn.execute("INSERT INTO t (v) VALUES ('x');")
        self.assertTrue(os.path.exists(path + "-wal"))
        self.assertTrue(os.path.exists(path + "-shm"))
        conn.close()

    def test_a_writer_no_longer_blocks_a_reader(self):
        """The ingest holding the database open is what broke rating. Under
        delete a commit takes an exclusive lock and readers fail; under WAL
        the reader is untouched."""
        import sqlite3

        for mode, reader_survives in (("delete", False), ("wal", True)):
            with self.subTest(mode=mode):
                path, actual = self.open_probe(mode)
                self.assertEqual(actual, mode)
                writer = sqlite3.connect(path, timeout=0.2, isolation_level=None)
                if mode == "wal":
                    writer.execute("PRAGMA journal_mode=WAL;")
                writer.execute("BEGIN EXCLUSIVE;")
                writer.execute("INSERT INTO t (v) VALUES ('held');")
                reader = sqlite3.connect(path, timeout=0.2, isolation_level=None)
                try:
                    reader.execute("SELECT COUNT(*) FROM t;").fetchone()
                    read_worked = True
                except sqlite3.OperationalError:
                    read_worked = False
                self.assertIs(read_worked, reader_survives)
                writer.execute("ROLLBACK;")
                writer.close()
                reader.close()

    def test_a_reader_no_longer_blocks_a_writer(self):
        """The mirror case, and the one Patricio hit: a page holding a read
        transaction open made the rating write fail under delete."""
        import sqlite3

        for mode, write_survives in (("delete", False), ("wal", True)):
            with self.subTest(mode=mode):
                path, _ = self.open_probe(mode)
                reader = sqlite3.connect(path, timeout=0.2, isolation_level=None)
                if mode == "wal":
                    reader.execute("PRAGMA journal_mode=WAL;")
                reader.execute("BEGIN;")
                reader.execute("SELECT COUNT(*) FROM t;").fetchone()
                writer = sqlite3.connect(path, timeout=0.2, isolation_level=None)
                try:
                    writer.execute("BEGIN IMMEDIATE;")
                    writer.execute("INSERT INTO t (v) VALUES ('during-read');")
                    writer.execute("COMMIT;")
                    wrote = True
                except sqlite3.OperationalError:
                    wrote = False
                self.assertIs(wrote, write_survives)
                reader.close()
                writer.close()

    def test_a_second_writer_still_waits_its_turn(self):
        """WAL does not give SQLite two writers. It gives the loser a queue,
        which is what `timeout` is for: it waits rather than failing at once."""
        import sqlite3
        import time

        path, _ = self.open_probe("wal")
        first = sqlite3.connect(path, timeout=0.2, isolation_level=None)
        first.execute("PRAGMA journal_mode=WAL;")
        first.execute("BEGIN IMMEDIATE;")
        first.execute("INSERT INTO t (v) VALUES ('held');")
        second = sqlite3.connect(path, timeout=0.5, isolation_level=None)
        started = time.monotonic()
        with self.assertRaises(sqlite3.OperationalError):
            second.execute("INSERT INTO t (v) VALUES ('queued');")
        # It waited for the timeout instead of giving up immediately.
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        first.execute("ROLLBACK;")
        first.close()
        second.close()


class AlreadyWatchedFilterTests(TestCase):
    """ "More shows like this" stopped recommending what the reader rated (#27).

    A rating says you have seen it, so recommending it back tells someone to
    watch what they just finished. The half that matters is the backfill: the
    row has to pull the next candidate down rather than quietly get shorter.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("rater", password="pw-rater-123")
        cls.stranger = User.objects.create_user("stranger", password="pw-str-123")
        cls.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        # One shared lead ties every candidate to the source, so Layer 1
        # connects all of them and the store fills past the opening rung.
        person = Person.objects.create(tmdb_id=1, name="Shared Lead")
        CastMember.objects.create(
            show=cls.source,
            person=person,
            order=0,
            character="Lead",
            episode_count=10,
        )
        cls.candidates = []
        for i in range(10):
            show = Show.objects.create(
                tmdb_id=100 + i,
                name=f"Candidate {i}",
                number_of_episodes=10,
                # Descending vote_average keeps the Layer 1 order stable and
                # readable, so "the next one down" is a nameable show.
                vote_average=9.0 - i * 0.1,
                vote_count=100,
            )
            CastMember.objects.create(
                show=show,
                person=person,
                order=0,
                character="Lead",
                episode_count=10,
            )
            cls.candidates.append(show)
        call_command("rebuild_similar_shows", stdout=StringIO())

    def shown(self, step=None):
        url = self.source.get_absolute_url()
        if step:
            url += f"?show={step}"
        resp = self.client.get(url)
        return [r["show"] for r in resp.context["recommendations"]]

    def test_baseline_order_before_anyone_rates_anything(self):
        """What the row looks like untouched, so the rest of these mean something."""
        self.assertEqual(len(self.shown()), DETAIL_RECOMMENDATION_LIMIT)

    def test_a_rated_show_is_not_recommended(self):
        self.client.login(username="rater", password="pw-rater-123")
        top = self.shown()[0]
        Rating.objects.create(user=self.user, show=top, score=4.5)
        self.assertNotIn(top, self.shown())

    def test_the_row_does_not_shrink_when_a_show_is_filtered_out(self):
        """The backfill. Three in, three out, with the fourth pulled down."""
        self.client.login(username="rater", password="pw-rater-123")
        before = self.shown()
        Rating.objects.create(user=self.user, show=before[0], score=4.5)
        after = self.shown()
        self.assertEqual(len(after), len(before))
        self.assertEqual(len(after), DETAIL_RECOMMENDATION_LIMIT)
        # The survivors keep their order and the next candidate joins the end.
        self.assertEqual(after[:2], before[1:])
        self.assertNotIn(after[2], before)

    def test_filtering_does_not_reorder_the_survivors(self):
        """Removing a row must only shorten the list, never permute it. rerank
        derives gravity from a candidate's position in a list of length n, so
        filtering before the re-rank would shift the shows ahead of the dropped
        one and change the answer."""
        self.client.login(username="rater", password="pw-rater-123")
        full = [s.pk for s in self.shown(step=12)]
        Rating.objects.create(user=self.user, show=self.candidates[3], score=4.5)
        after = [s.pk for s in self.shown(step=12)]
        self.assertEqual(after, [pk for pk in full if pk != self.candidates[3].pk])

    def test_several_ratings_all_get_filtered(self):
        self.client.login(username="rater", password="pw-rater-123")
        for show in self.shown()[:3]:
            Rating.objects.create(user=self.user, show=show, score=4.0)
        names = {s.name for s in self.shown()}
        self.assertEqual(len(names), DETAIL_RECOMMENDATION_LIMIT)
        self.assertFalse(
            names
            & {"Candidate 0", "Candidate 1", "Candidate 2"}
            & {s.name for s in Show.objects.watched_by(self.user)}
        )

    def test_watch_history_filters_too_even_with_no_rating(self):
        """watched_by is rating OR logged episodes (ADR-08). WatchHistory has
        no rows today, so this is the half of the rule nothing else exercises."""
        self.client.login(username="rater", password="pw-rater-123")
        target = self.shown()[0]
        season = Season.objects.create(show=target, tmdb_id=900, season_number=1)
        episode = Episode.objects.create(season=season, tmdb_id=9001, episode_number=1)
        WatchHistory.objects.create(user=self.user, episode=episode)
        self.assertNotIn(target, self.shown())

    def test_another_readers_ratings_do_not_filter_my_row(self):
        Rating.objects.create(user=self.stranger, show=self.candidates[0], score=5.0)
        self.client.login(username="rater", password="pw-rater-123")
        self.assertIn(self.candidates[0], self.shown())

    def test_an_anonymous_reader_sees_the_unfiltered_row(self):
        Rating.objects.create(user=self.user, show=self.candidates[0], score=5.0)
        self.assertIn(self.candidates[0], self.shown())

    def test_the_ladder_stops_offering_rungs_that_no_longer_exist(self):
        """With eleven candidates the ladder can reach 9. Rate enough of them
        away and the climb has to end sooner rather than offer an empty step."""
        self.client.login(username="rater", password="pw-rater-123")
        for show in self.candidates[:8]:
            Rating.objects.create(user=self.user, show=show, score=4.0)
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(resp.context["recommendations_available"], 2)
        self.assertIsNone(resp.context["next_recommendation_step"])
        self.assertEqual(len(resp.context["recommendations"]), 2)

    def test_my_ratings_is_never_filtered(self):
        """It exists to show the reader what they rated. Filtering it would
        empty the page it is."""
        self.client.login(username="rater", password="pw-rater-123")
        Rating.objects.create(user=self.user, show=self.candidates[0], score=4.5)
        resp = self.client.get(reverse("shows:my_ratings"))
        self.assertIn(self.candidates[0].pk, [s.pk for s in resp.context["shows"]])

    def test_top_picks_is_never_filtered(self):
        """Top Picks is built FROM the user's ratings. The same filter applied
        there would leave the row permanently empty."""
        self.client.login(username="rater", password="pw-rater-123")
        for show in self.candidates[:3]:
            Rating.objects.create(user=self.user, show=show, score=4.5)
        resp = self.client.get(reverse("shows:index"))
        self.assertTrue(resp.context["top_picks"])

    def test_side_quests_already_excluded_watched(self):
        """Not a new fix: side_quests() has always blocked watched shows, and
        this pins that it stays true so #27 is not re-opened against it."""
        self.client.login(username="rater", password="pw-rater-123")
        for show in self.candidates[:4]:
            Rating.objects.create(user=self.user, show=show, score=4.5)
        resp = self.client.get(reverse("shows:index"))
        quest_pks = {s.pk for s in resp.context["side_quests"]}
        watched_pks = set(Show.objects.watched_by(self.user).values_list("pk", flat=True))
        self.assertFalse(quest_pks & watched_pks)

    def test_without_watched_keeps_the_ranked_shape(self):
        """It composes either side of rerank, so mode and the re-rank's own
        attributes have to survive the filter."""
        Rating.objects.create(user=self.user, show=self.candidates[0], score=4.5)
        ranked = rerank(self.user, stored_similar(self.source))
        filtered = without_watched(self.user, ranked)
        self.assertEqual(filtered.mode, ranked.mode)
        self.assertEqual(filtered.personalized, ranked.personalized)
        self.assertIs(filtered.profile, ranked.profile)
        self.assertEqual(len(filtered), len(ranked) - 1)


class ConnectionTypePreferenceTests(TestCase):
    """What a user's own ratings say about cast connections versus crew ones.

    Issue #7. Five rated pairs held together by a shared lead on every episode
    of both, and five by a shared creator, each pair a mutual Layer 1 edge.

    Five and not two, because the gate is a permutation test now (ADR-15): it
    asks whether these ratings spread differently over this same graph would
    still look like a preference. With two pairs there are only six ways to
    split four shows, so the most extreme possible result carries p = 0.167 and
    could never clear a 95% bar however cleanly the ratings separated. Ten
    shows give 252 arrangements and a perfect split lands at p = 0.004.

    That is the fixture matching the contract rather than the contract bending
    to the fixture: a preference read off four shows was never evidence.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("typed", password="pw-typed-9x")
        cls.lead = Person.objects.create(tmdb_id=1, name="Shared Lead")
        cls.creator = Person.objects.create(tmdb_id=2, name="Shared Creator")

        def show(tmdb_id, name):
            return Show.objects.create(
                tmdb_id=tmdb_id, name=name, number_of_episodes=10, vote_average=8.0
            )

        # Five pairs a side. Each pair shares its own person, so the pairs are
        # edges to each other and not to the rest, which keeps every edge in
        # the set purely cast or purely crew.
        cls.cast_shows, cls.crew_shows = [], []
        for i in range(5):
            a, b = show(10 + i * 2, f"Cast A{i}"), show(11 + i * 2, f"Cast B{i}")
            lead = Person.objects.create(tmdb_id=100 + i, name=f"Lead {i}")
            for s in (a, b):
                CastMember.objects.create(
                    show=s, person=lead, order=0, character="Hero", episode_count=10
                )
            cls._edge(a, b)
            cls._edge(b, a)
            cls.cast_shows += [a, b]

            c, d = show(50 + i * 2, f"Crew A{i}"), show(51 + i * 2, f"Crew B{i}")
            maker = Person.objects.create(tmdb_id=200 + i, name=f"Creator {i}")
            for s in (c, d):
                CrewMember.objects.create(show=s, person=maker, job="Creator", episode_count=10)
            cls._edge(c, d)
            cls._edge(d, c)
            cls.crew_shows += [c, d]

        # Kept for the tests that name a single show.
        cls.cast_a, cls.cast_b = cls.cast_shows[0], cls.cast_shows[1]
        cls.crew_a, cls.crew_b = cls.crew_shows[0], cls.crew_shows[1]

    @classmethod
    def _edge(cls, source, target):
        # The cast/crew split is written at rebuild time on a real edge
        # (ADR-07), so the fixture computes it the same way rather than
        # leaving it at zero, which would be an edge no rebuild could produce.
        indexes = role_indexes([source, target])
        mass = {"cast": 0.0, "crew": 0.0}
        for c in shared_connections(source, indexes[source.id], target, indexes[target.id]):
            mass[connection_type(c.kind)] += c.contribution
        SimilarShow.objects.create(
            source=source,
            target=target,
            rank=0,
            score=1.0,
            shared_people=1,
            cast_contribution=mass["cast"],
            crew_contribution=mass["crew"],
            mode="weighted",
        )

    def _rate(self, cast_score, crew_score):
        for s in self.cast_shows:
            Rating.objects.create(user=self.user, show=s, score=cast_score)
        for s in self.crew_shows:
            Rating.objects.create(user=self.user, show=s, score=crew_score)
        return build_profile(self.user)

    def test_cold_start_has_no_connection_type_opinion(self):
        profile = build_profile(self.user)
        self.assertTrue(profile.is_cold_start)
        self.assertEqual(profile.connection_type_weights, {})
        self.assertEqual(profile.connection_type_lean, 0.0)
        self.assertIsNone(profile.leans_toward)

    def test_anonymous_visitor_has_no_connection_type_opinion(self):
        profile = build_profile(AnonymousUser())
        self.assertEqual(profile.connection_type_weights, {})
        self.assertEqual(profile.connection_type_lean, 0.0)

    def test_cast_leaning_user(self):
        # 5.0 on the cast-tied pair is a +2.0 signal, 3.5 on the crew-tied pair
        # is +0.5: the affinities are the signals those connections earned.
        profile = self._rate(cast_score=5.0, crew_score=3.5)
        self.assertAlmostEqual(profile.connection_type_weights["cast"], 2.0)
        self.assertAlmostEqual(profile.connection_type_weights["crew"], 0.5)
        self.assertAlmostEqual(profile.connection_type_lean, 1.5)
        self.assertEqual(profile.leans_toward, "cast")

    def test_crew_leaning_user(self):
        profile = self._rate(cast_score=3.5, crew_score=5.0)
        self.assertAlmostEqual(profile.connection_type_weights["cast"], 0.5)
        self.assertAlmostEqual(profile.connection_type_weights["crew"], 2.0)
        self.assertAlmostEqual(profile.connection_type_lean, -1.5)
        self.assertEqual(profile.leans_toward, "crew")

    def test_a_low_rating_signs_the_lean_negative(self):
        # A 1-star rating is information (ADR-08): disliking the crew-tied pair
        # is a reason to name cast first, not merely a weaker positive.
        profile = self._rate(cast_score=4.0, crew_score=1.0)
        self.assertAlmostEqual(profile.connection_type_weights["crew"], -2.0)
        self.assertEqual(profile.leans_toward, "cast")

    def test_equal_ratings_earn_no_lean_however_many_edges(self):
        # The user loves everything. Both types earned the same signal, so
        # there is no preference to assert and the gap gate holds it at zero.
        profile = self._rate(cast_score=5.0, crew_score=5.0)
        self.assertEqual(profile.connection_type_weights, {})
        self.assertEqual(profile.connection_type_lean, 0.0)

    def test_a_gap_below_the_floor_is_not_called(self):
        # A tenth of a star apart, and perfectly consistent across all ten
        # shows. The permutation test would call that significant, correctly:
        # it IS a reliable difference. MIN_CONNECTION_TYPE_LEAN is the separate
        # judgment that a difference this small is not worth reporting however
        # real it is, which is why the floor survives the move to shuffling.
        profile = self._rate(cast_score=5.0, crew_score=4.9)
        self.assertGreater(MIN_CONNECTION_TYPE_LEAN, 0.1)
        self.assertEqual(profile.connection_type_weights, {})

    def test_too_few_edges_inside_the_rated_set_is_not_called(self):
        # Rating one pair leaves two directed edges, under the floor, and the
        # gap between them would otherwise be enormous.
        Rating.objects.create(user=self.user, show=self.cast_a, score=5.0)
        Rating.objects.create(user=self.user, show=self.cast_b, score=5.0)
        profile = build_profile(self.user)
        self.assertEqual(profile.rating_count, 2)
        self.assertFalse(profile.is_cold_start)
        self.assertEqual(profile.connection_type_weights, {})
        self.assertEqual(profile.connection_type_lean, 0.0)

    def test_ratings_with_no_edges_between_them_are_not_called(self):
        # Ten ratings that share nothing say nothing about connection types.
        # Measured on the real catalog: ten shows picked at random have zero
        # Layer 1 edges between them, so this is the common case, not the edge.
        for i in range(10):
            s = Show.objects.create(
                tmdb_id=100 + i,
                name=f"Lonely {i}",
                number_of_episodes=10,
                vote_average=8.0,
            )
            Rating.objects.create(user=self.user, show=s, score=5.0 if i % 2 else 1.0)
        profile = build_profile(self.user)
        self.assertEqual(profile.rating_count, 10)
        self.assertEqual(profile.connection_type_weights, {})

    def test_a_watched_but_unrated_show_does_not_vote(self):
        # WATCHED_SIGNAL is a constant, so counting views would pull both
        # affinities toward the same number and wash out the difference.
        self._rate(cast_score=5.0, crew_score=3.5)
        extra = Show.objects.create(
            tmdb_id=200, name="Watched Only", number_of_episodes=10, vote_average=8.0
        )
        season = Season.objects.create(show=extra, tmdb_id=1, season_number=1)
        episode = Episode.objects.create(season=season, tmdb_id=1, episode_number=1, name="E1")
        WatchHistory.objects.create(user=self.user, episode=episode)
        self.assertAlmostEqual(build_profile(self.user).connection_type_lean, 1.5)

    def test_the_lean_never_reaches_the_show_ranking(self):
        # Layer 2 owns genre and tag preference; this dimension orders NAMES in
        # a callout and must not become a third term in score_for (ADR-08).
        profile = self._rate(cast_score=5.0, crew_score=3.5)
        blank = Show.objects.create(
            tmdb_id=300, name="Blank", number_of_episodes=10, vote_average=8.0
        )
        self.assertEqual(profile.score_for(blank), 0.0)

    def test_minimum_edge_floor_is_the_documented_one(self):
        self.assertEqual(MIN_CONNECTION_TYPE_EDGES, 4)


class ConnectionTypeNamingTests(TestCase):
    """Ordering the named connections by a learned lean (issue #7).

    The source shares a marquee crew member on the whole run of both shows and
    a recognizable lead on rather less of it, so by score alone the crew credit
    is named first. A cast-leaning reader flips that; a crew-leaning one does
    not; and a blowout crew edge survives either way.
    """

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        cls.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=10)
        cls.lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        cls.maker = Person.objects.create(tmdb_id=2, name="The Maker")

    def _profile(self, cast, crew):
        return PreferenceProfile({}, {}, {}, {}, 4, {"cast": cast, "crew": crew})

    def _connections(self, lead_episodes, maker_episodes):
        for show in (self.source, self.cand):
            CastMember.objects.create(
                show=show,
                person=self.lead,
                order=0,
                character="Hero",
                episode_count=lead_episodes,
            )
            CrewMember.objects.create(
                show=show,
                person=self.maker,
                job="Creator",
                episode_count=maker_episodes,
            )
        return shared_connections(
            self.source,
            role_index(self.source),
            self.cand,
            role_index(self.cand),
        )

    def test_no_profile_keeps_the_score_order(self):
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        named, others = name_connections(conns)
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])
        self.assertEqual(others, 0)

    def test_a_flat_profile_keeps_the_score_order(self):
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        named, _ = name_connections(conns, profile=build_profile(AnonymousUser()))
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])

    def test_a_cast_leaning_reader_hears_the_actor_first(self):
        # 0.6 * 1.5 = 0.90 beats 1.0 * 0.5 = 0.50.
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        named, _ = name_connections(conns, profile=self._profile(2.0, 0.5))
        self.assertEqual([c.name for c in named], ["Lead Actor", "The Maker"])

    def test_a_crew_leaning_reader_keeps_the_creator_first(self):
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        named, _ = name_connections(conns, profile=self._profile(0.5, 2.0))
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])

    def test_a_dominant_edge_resists_the_lean(self):
        # 0.2 * 1.5 = 0.30 still loses to 1.0 * 0.5 = 0.50. A tilt reorders the
        # near-ties, it does not overturn what Layer 1 actually measured.
        conns = self._connections(lead_episodes=2, maker_episodes=10)
        named, _ = name_connections(conns, profile=self._profile(2.0, 0.5))
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])

    def test_the_lean_can_drop_a_credit_out_of_the_named_few(self):
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        named, others = name_connections(conns, max_named=1, profile=self._profile(2.0, 0.5))
        self.assertEqual([c.name for c in named], ["Lead Actor"])
        self.assertEqual(others, 1)

    def test_the_lean_never_changes_how_many_people_are_counted(self):
        conns = self._connections(lead_episodes=6, maker_episodes=10)
        plain = name_connections(conns)
        tilted = name_connections(conns, profile=self._profile(2.0, 0.5))
        self.assertEqual({c.name for c in plain[0]}, {c.name for c in tilted[0]})
        self.assertEqual(plain[1], tilted[1])

    def test_marquee_and_plain_crew_are_one_type(self):
        self.assertEqual(connection_type("cast"), "cast")
        self.assertEqual(connection_type("marquee"), "crew")
        self.assertEqual(connection_type("crew"), "crew")


class RoleIndexesBulkTests(TestCase):
    """role_indexes is role_index for a set, in a fixed number of queries.

    The detail page indexes every candidate it renders and Layer 2 indexes every
    show in a user's rated set, so the per-show form was two queries times N.
    Verified against the live 464-show catalog on 2026-08-26: identical
    RoleInfo for all 280,229 person-show entries.
    """

    @classmethod
    def setUpTestData(cls):
        cls.a = Show.objects.create(tmdb_id=1, name="A", number_of_episodes=10)
        cls.b = Show.objects.create(tmdb_id=2, name="B", number_of_episodes=10)
        cls.empty = Show.objects.create(tmdb_id=3, name="Empty", number_of_episodes=10)
        lead = Person.objects.create(tmdb_id=1, name="Lead")
        maker = Person.objects.create(tmdb_id=2, name="Maker")
        caster = Person.objects.create(tmdb_id=3, name="Caster")
        CastMember.objects.create(
            show=cls.a, person=lead, order=0, character="Hero", episode_count=10
        )
        CrewMember.objects.create(show=cls.b, person=maker, job="Creator", episode_count=10)
        # Service jobs stay excluded through the bulk path too (ADR-01).
        CrewMember.objects.create(show=cls.b, person=caster, job="Casting", episode_count=10)

    def test_bulk_matches_the_one_show_form(self):
        shows = [self.a, self.b, self.empty]
        bulk = role_indexes(shows)
        self.assertEqual(bulk, {s.id: role_index(s) for s in shows})

    def test_a_show_with_nobody_still_gets_an_entry(self):
        self.assertEqual(role_indexes([self.empty])[self.empty.id], {})

    def test_service_jobs_stay_excluded(self):
        names = {info.name for info in role_indexes([self.b])[self.b.id].values()}
        self.assertEqual(names, {"Maker"})

    def test_the_whole_set_costs_a_fixed_number_of_queries(self):
        with self.assertNumQueries(2):
            role_indexes([self.a, self.b, self.empty])


class CalloutOrderingTests(TestCase):
    """Which block opens the sentence is the reader's, not the catalog's.

    Issue #7, amending issue #2's fixed "pitch by cast" order on 2026-08-26. A
    reader whose ratings say their shows hang together on crew hears the crew
    first; every reader without an earned lean gets the same cast-first
    sentence as before, by the same path rather than a repaired one.
    """

    def setUp(self):
        self.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        self.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=10)
        lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        maker = Person.objects.create(tmdb_id=2, name="The Maker")
        for show in (self.source, self.cand):
            CastMember.objects.create(
                show=show, person=lead, order=0, character="Hero", episode_count=10
            )
            CrewMember.objects.create(show=show, person=maker, job="Creator", episode_count=10)

    def _profile(self, cast, crew):
        return PreferenceProfile({}, {}, {}, {}, 4, {"cast": cast, "crew": crew})

    def _text(self, profile=None):
        conns = shared_connections(
            self.source,
            role_index(self.source),
            self.cand,
            role_index(self.cand),
        )
        named, others = name_connections(conns, profile=profile)
        callout = compose_callout(self.source, self.cand, conns, named, others, profile=profile)
        return "".join(seg["v"] for seg in callout["segments"])

    def test_no_profile_opens_on_cast(self):
        self.assertTrue(self._text().startswith("Lead Actor plays Hero"))

    def test_an_unearned_lean_takes_the_identical_default_path(self):
        # Cold start, insufficient signal and anonymous all arrive as a 0.0
        # lean, so this is the same sentence, not an approximation of it.
        default = self._text()
        for profile in (
            build_profile(AnonymousUser()),
            self._profile(1.0, 1.0),
            PreferenceProfile({}, {}, {}, {}, 7, {}),
        ):
            self.assertEqual(self._profile(0, 0).connection_type_lean, 0.0)
            self.assertEqual(self._text(profile=profile), default)

    def test_a_cast_leaning_reader_opens_on_cast(self):
        self.assertTrue(self._text(profile=self._profile(2.0, 0.5)).startswith("Lead Actor"))

    def test_a_crew_leaning_reader_opens_on_crew(self):
        text = self._text(profile=self._profile(0.5, 2.0))
        self.assertTrue(text.startswith("Creator The Maker created both"), text)
        self.assertIn("Lead Actor plays Hero", text)

    def test_opening_on_crew_still_capitalizes_the_sentence(self):
        # The crew clause opens on a lowercase role noun; the cast clause opens
        # on a name and needs no help.
        self.assertTrue(self._text(profile=self._profile(0.5, 2.0))[0].isupper())

    def test_the_lean_reorders_and_never_drops_anyone(self):
        cast_first = self._text(profile=self._profile(2.0, 0.5))
        crew_first = self._text(profile=self._profile(0.5, 2.0))
        self.assertNotEqual(cast_first, crew_first)
        for name in ("Lead Actor", "The Maker"):
            self.assertIn(name, cast_first)
            self.assertIn(name, crew_first)

    def test_the_tail_count_is_untouched_by_the_lean(self):
        for i in range(4):
            extra = Person.objects.create(tmdb_id=50 + i, name=f"Extra {i}")
            for show in (self.source, self.cand):
                CastMember.objects.create(
                    show=show, person=extra, order=600, character="Waiter", episode_count=1
                )
        self.assertIn("with 4 others", self._text())
        self.assertIn("with 4 others", self._text(profile=self._profile(0.5, 2.0)))


class CrewRoleCollapseTests(TestCase):
    """One role noun per clause, however many people held the role (issue #4).

    Four shared directors used to read "director W directed one episode,
    director X directed one episode, director Y..." Only the strongest holder
    of a role keeps the full clause now; the rest collapse behind it, exactly
    as the cast side has always done with "and Leslie Hope appears too".
    """

    def setUp(self):
        self.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=20)
        self.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=20)
        self._pid = 0

    def _shared_crew(self, name, job, src_eps, cand_eps=None):
        self._pid += 1
        person = Person.objects.create(tmdb_id=self._pid, name=name)
        CrewMember.objects.create(show=self.source, person=person, job=job, episode_count=src_eps)
        CrewMember.objects.create(
            show=self.cand, person=person, job=job, episode_count=cand_eps or src_eps
        )

    def _text(self):
        conns = shared_connections(
            self.source,
            role_index(self.source),
            self.cand,
            role_index(self.cand),
        )
        named, others = name_connections(conns)
        callout = compose_callout(self.source, self.cand, conns, named, others)
        return "".join(seg["v"] for seg in callout["segments"])

    def test_two_directors_say_director_once(self):
        self._shared_crew("Ana Reyes", "Director", 12)
        self._shared_crew("Ben Cole", "Director", 4)
        text = self._text()
        self.assertEqual(text.lower().count("director"), 1, text)
        self.assertIn("Ana Reyes directed 12 episodes", text)
        self.assertIn("Ben Cole directed too", text)

    def test_the_strongest_holder_keeps_the_full_clause(self):
        self._shared_crew("Ben Cole", "Director", 4)
        self._shared_crew("Ana Reyes", "Director", 12)
        text = self._text()
        self.assertIn("Director Ana Reyes directed 12 episodes", text)
        self.assertLess(text.index("Ana Reyes"), text.index("Ben Cole"))

    def test_four_directors_collapse_into_one_clause(self):
        for name, eps in (("Ana Reyes", 12), ("Ben Cole", 8), ("Cara Diaz", 4), ("Dev Okafor", 2)):
            self._shared_crew(name, "Director", eps)
        text = self._text()
        self.assertEqual(text.lower().count("director"), 1, text)
        self.assertIn("Ben Cole, Cara Diaz and Dev Okafor directed too", text)

    def test_different_roles_keep_their_own_clauses(self):
        self._shared_crew("Ana Reyes", "Creator", 20)
        self._shared_crew("Ben Cole", "Director", 12)
        text = self._text()
        self.assertIn("Creator Ana Reyes created both", text)
        self.assertIn("director Ben Cole directed 12 episodes", text)

    def test_roles_that_read_alike_collapse_together(self):
        # "Original Music Composer" and "Composer" are two TMDb jobs that both
        # read "composer", so grouping is by the prose noun rather than the raw
        # job. Otherwise the sentence says "composer" twice for one role.
        self._shared_crew("Ana Reyes", "Original Music Composer", 20)
        self._shared_crew("Ben Cole", "Composer", 20)
        text = self._text()
        self.assertEqual(text.lower().count("composer"), 1, text)
        self.assertIn("Ben Cole scored too", text)

    def test_a_lone_role_holder_reads_exactly_as_before(self):
        self._shared_crew("Ben Cole", "Director", 12)
        self.assertIn("Director Ben Cole directed 12 episodes", self._text())

    def test_an_unlisted_job_still_collapses(self):
        # A job outside ROLE_PROSE takes the generic phrasing on both halves,
        # so the callout never breaks on a job nobody anticipated.
        #
        # The job here must be absent from SERVICE_JOBS as well as ROLE_PROSE.
        # This test originally used "Animal Wrangler", which ADR-01's 2026-08-26
        # amendment later excluded as an on-set vendor, so the crew member left
        # the graph entirely and the callout came back empty. "Storyboard Artist"
        # carries 760 real credits and sits on neither list.
        self._shared_crew("Ana Reyes", "Storyboard Artist", 20)
        self._shared_crew("Ben Cole", "Storyboard Artist", 10)
        text = self._text()
        self.assertIn("Storyboard artist Ana Reyes worked on both", text)
        self.assertIn("Ben Cole worked on it too", text)


class WatchNextTests(TestCase):
    """Watch Next (#24): unwatched shows reachable from what you already like.

    Top Picks ranks shows the user has already rated, so a reader who finishes a
    show and comes back to the home page is shown their own history. These
    freeze the row that answers the question instead: seeds are ratings at or
    above the floor, every seed's edges are scaled by how much the user liked it,
    a candidate's score is the SUM across seeds, and nothing already watched can
    appear. SimilarShow is written directly because that table is Layer 2's
    input (ADR-07).
    """

    @classmethod
    def setUpTestData(cls):
        drama = Genre.objects.create(tmdb_id=1, name="Drama")

        def show(tmdb_id, name):
            s = Show.objects.create(
                tmdb_id=tmdb_id, name=name, number_of_episodes=10, popularity=1.0
            )
            s.genres.set([drama])
            return s

        cls.loved = show(1, "Loved")
        cls.liked = show(2, "Liked")
        cls.meh = show(3, "Meh")

        # Reached from both seeds, so its score sums. Reached from one seed
        # only, at a higher single edge, so it must still lose.
        cls.both = show(4, "ReachedByBoth")
        cls.one = show(5, "ReachedByOne")
        cls.from_meh = show(6, "ReachedFromMeh")
        cls.already_seen = show(7, "AlreadySeen")

        def edge(source, target, score, rank=1):
            SimilarShow.objects.create(
                source=source,
                target=target,
                rank=rank,
                score=score,
                shared_people=1,
                mode="weighted",
            )

        edge(cls.loved, cls.both, 4.0)
        edge(cls.liked, cls.both, 4.0, rank=2)
        edge(cls.loved, cls.one, 5.0, rank=3)
        edge(cls.meh, cls.from_meh, 9.0)
        edge(cls.loved, cls.already_seen, 9.0, rank=4)

        cls.user = User.objects.create_user("watcher", password="x")
        Rating.objects.create(user=cls.user, show=cls.loved, score=5.0)
        Rating.objects.create(user=cls.user, show=cls.liked, score=4.0)
        Rating.objects.create(user=cls.user, show=cls.meh, score=2.0)
        Rating.objects.create(user=cls.user, show=cls.already_seen, score=3.0)

    def test_anonymous_reader_gets_nothing(self):
        self.assertEqual(list(watch_next(AnonymousUser())), [])

    def test_a_reader_with_no_seed_above_the_floor_gets_nothing(self):
        cold = User.objects.create_user("cold", password="x")
        Rating.objects.create(user=cold, show=self.loved, score=3.5)
        self.assertEqual(list(watch_next(cold)), [])

    def test_a_show_reached_from_two_seeds_beats_a_stronger_single_edge(self):
        """The whole reason scores sum instead of taking the best edge.

        ReachedByBoth has two weaker edges: 4.0 from a loved seed (weight 2.0)
        plus 4.0 from a liked one (weight 1.0), so 12.0. ReachedByOne has one
        stronger edge, 5.0 from the loved seed, so 10.0. Being connected to two
        shows you like has to beat being connected harder to one, or the row
        cannot claim to reason the way a reader does.
        """
        names = [s.name for s in watch_next(self.user)]
        self.assertLess(names.index("ReachedByBoth"), names.index("ReachedByOne"))

    def test_the_seed_rating_scales_its_edges(self):
        by_name = {s.name: s.score for s in watch_next(self.user)}
        # loved (5.0) weighs 2.0, liked (4.0) weighs 1.0.
        self.assertAlmostEqual(by_name["ReachedByBoth"], 4.0 * 2.0 + 4.0 * 1.0)
        self.assertAlmostEqual(by_name["ReachedByOne"], 5.0 * 2.0)

    def test_a_show_below_the_seed_floor_contributes_nothing(self):
        names = [s.name for s in watch_next(self.user)]
        self.assertNotIn("ReachedFromMeh", names)

    def test_a_watched_show_never_appears(self):
        names = [s.name for s in watch_next(self.user)]
        self.assertNotIn("AlreadySeen", names)

    def test_the_seeds_themselves_never_appear(self):
        names = [s.name for s in watch_next(self.user)]
        for seed in ("Loved", "Liked"):
            self.assertNotIn(seed, names)

    def test_excluded_ids_are_honored(self):
        names = [s.name for s in watch_next(self.user, exclude_ids={self.one.pk})]
        self.assertNotIn("ReachedByOne", names)
        self.assertIn("ReachedByBoth", names)

    def test_the_row_reports_it_was_personalized(self):
        result = watch_next(self.user)
        self.assertTrue(result.personalized)
        self.assertEqual(result.mode, "weighted")

    def test_an_empty_row_says_which_kind_of_empty_it_is(self):
        """A reader with no seed is asked to rate. A reader whose seeds reached
        nothing must not be, because they already did (#24)."""
        cold = User.objects.create_user("nothingyet", password="x")
        self.assertFalse(watch_next(cold).has_seeds)

        stranded = User.objects.create_user("stranded", password="x")
        alone = Show.objects.create(tmdb_id=99, name="Alone", number_of_episodes=10, popularity=1.0)
        Rating.objects.create(user=stranded, show=alone, score=5.0)
        result = watch_next(stranded)
        self.assertEqual(list(result), [])
        self.assertTrue(result.has_seeds)


class CatalogShipTests(TestCase):
    """A catalog ship is a merge, and spoken-for shows are undeletable (#28).

    The catalog is TMDb's and read-only; ratings, reviews, watchlist rows and
    tags are the user's. A catalog update must therefore be a merge in place,
    never a replacement, and the invariant these tests freeze is: no command
    may delete a Show that any user row points at. Today that protection is an
    implementation detail of prune_low_signal's spoken_for set; these tests
    are what turn it into a contract a refactor cannot silently drop.

    Identity is the other half (ADR-03): ingest_show pins pk == tmdb_id, so a
    re-ingest corrects a show's record without re-pointing anyone's rating.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("alice", password="pw-alice-123")
        low = MIN_VOTE_COUNT - 90  # comfortably under the floor

        def make(i, name):
            return Show.objects.create(
                tmdb_id=i, id=i, name=name, vote_count=low, number_of_episodes=8
            )
        cls.rated = make(9001, "Rated but unpopular")
        cls.reviewed = make(9002, "Reviewed but unpopular")
        cls.listed = make(9003, "Watchlisted but unpopular")
        cls.tagged = make(9004, "Tagged but unpopular")
        cls.unloved = make(9005, "Unloved and unpopular")
        cls.healthy = Show.objects.create(
            tmdb_id=9006, id=9006, name="Healthy", vote_count=5000, number_of_episodes=8
        )

        Rating.objects.create(user=cls.alice, show=cls.rated, score=5.0)
        Review.objects.create(user=cls.alice, show=cls.reviewed, title="Kept", body="…")
        Watchlist.objects.create(user=cls.alice, show=cls.listed)
        cozy = Tag.objects.create(name="cozy", slug="cozy")
        ShowTag.objects.create(user=cls.alice, show=cls.tagged, tag=cozy)

    def test_prune_spares_every_spoken_for_kind(self):
        # Losing someone's 5.0 because a show got less popular is the one
        # outcome prune must never produce, and the same holds for the three
        # other ways a user can point at a show.
        out = StringIO()
        call_command("prune_low_signal", "--skip-rebuild", stdout=out)

        survivors = set(Show.objects.values_list("tmdb_id", flat=True))
        self.assertNotIn(9005, survivors)  # nothing spoke for it
        for spared in (9001, 9002, 9003, 9004, 9006):
            self.assertIn(spared, survivors)

        self.assertEqual(Rating.objects.count(), 1)
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(Watchlist.objects.count(), 1)
        self.assertEqual(ShowTag.objects.count(), 1)

    def test_reingest_corrects_the_record_without_repointing_ratings(self):
        # A shipped catalog meets an existing database as update_or_create on
        # tmdb_id with pk pinned to it (ADR-03), so fresher data lands in the
        # same row the rating already points at.
        class FakeClient:
            def get_tv_details(self, tmdb_id):
                return {
                    "name": "Rated and renamed",
                    "vote_count": 4000,
                    "vote_average": 8.1,
                    "seasons": [],
                }

            def get_tv_aggregate_credits(self, tmdb_id):
                return {}

        rating_pk_before = Rating.objects.get(user=self.alice).pk
        show_pk_before = self.rated.pk

        Ingestor(client=FakeClient()).ingest_show(self.rated.tmdb_id)

        rating = Rating.objects.get(pk=rating_pk_before)
        self.assertEqual(rating.show_id, show_pk_before)
        self.assertEqual(rating.show.tmdb_id, 9001)
        self.assertEqual(rating.show.name, "Rated and renamed")
        self.assertEqual(rating.show.vote_count, 4000)
        self.assertEqual(rating.score, 5.0)

    def test_refresh_reports_a_fall_and_prune_spares_it(self):
        # The full ship cycle for the worst case: a rated show drops under the
        # floor. refresh_catalog must report without deleting, and the prune
        # that follows must spare it. The rating survives the whole cycle.
        from unittest.mock import patch

        Show.objects.filter(pk=self.rated.pk).update(vote_count=MIN_VOTE_COUNT + 400)
        # .update() dodges auto_now, making this the stalest show on purpose.
        Show.objects.filter(pk=self.rated.pk).update(updated_at="2020-01-01T00:00:00Z")

        class FallingIngestor:
            def ingest_show(self, tmdb_id):
                Show.objects.filter(tmdb_id=tmdb_id).update(vote_count=MIN_VOTE_COUNT - 60)

        out = StringIO()
        with patch(
            "shows.management.commands.refresh_catalog.Ingestor",
            return_value=FallingIngestor(),
        ):
            call_command("refresh_catalog", "--oldest", "1", "--skip-rebuild", stdout=out)

        report = out.getvalue()
        self.assertIn("fell below", report)
        self.assertIn("Not removed", report)
        self.assertTrue(Show.objects.filter(tmdb_id=9001).exists())

        call_command("prune_low_signal", "--skip-rebuild", stdout=StringIO())
        self.assertTrue(Show.objects.filter(tmdb_id=9001).exists())
        self.assertEqual(Rating.objects.count(), 1)


class FtsQueryEscapingTests(TestCase):
    """Freezes #29's named rabbit hole: a raw term never reaches MATCH raw.

    FTS5 MATCH is a query language. Double quotes delimit phrases, AND, OR
    and NOT are operators, and a bare hyphen or asterisk is syntax. Every
    term becomes one quoted phrase with a trailing star, safe by construction,
    so the escaping is what these tests freeze, not any particular match.
    """

    def test_an_apostrophe_survives_quoting(self):
        self.assertEqual(_fts_query("don't"), '"don\'t"*')

    def test_a_hyphen_is_text_not_an_operator(self):
        # Unquoted, spider-man parses as spider NOT man and excludes matches.
        self.assertEqual(_fts_query("spider-man"), '"spider-man"*')

    def test_an_embedded_quote_is_doubled(self):
        # SQL-style doubling: the one escape FTS5 understands inside a phrase.
        self.assertEqual(_fts_query('say "cheese"'), '"say ""cheese"""*')

    def test_surrounding_space_is_stripped(self):
        self.assertEqual(_fts_query("  padded  "), '"padded"*')

    def test_a_bare_star_produces_no_query(self):
        # The tokenizer keeps nothing of it, and FTS5 rejects an empty
        # phrase, so the helper refuses to build one rather than raising.
        self.assertIsNone(_fts_query("*"))
        self.assertIsNone(_fts_query("--"))
        self.assertIsNone(_fts_query("  "))

    def test_every_edge_case_executes_without_raising(self):
        # The point of the escaping: MATCH accepts whatever the helper built.
        for term in ["don't", "spider-man", 'say "cheese"', "*", "AND", "NOT near"]:
            _episode_fts(term)  # raises OperationalError if escaping is wrong


class EpisodeFtsSearchTests(TestCase):
    """Freezes #29's two decisions: prefix parity and bm25 within the branch.

    Prefix matching was measured identical to the \\b regex on every term
    tried, where exact matching silently lost "murderer" and "murders". And
    bm25 orders shows inside the episode bucket only: branch weights stay as
    ADR-12 left them, so a title hit still beats the best synopsis match.
    """

    @classmethod
    def setUpTestData(cls):
        def show(tmdb_id, name, vote_average):
            return Show.objects.create(
                tmdb_id=tmdb_id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                first_air_date="2010-01-01",
                vote_average=vote_average,
                vote_count=500,
                original_language="en",
                status="Ended",
            )

        def episode(show_obj, tmdb_id, overview):
            season = Season.objects.create(show=show_obj, tmdb_id=tmdb_id, season_number=1)
            return Episode.objects.create(
                season=season, tmdb_id=tmdb_id, episode_number=1, overview=overview
            )

        # bm25 should rank the synopsis about zeppelins above the synopsis
        # that mentions one, and the crowd score is set to disagree so the
        # old vote_average ordering would fail this test.
        cls.about = show(9101, "Airship Diaries", vote_average=5.0)
        episode(cls.about, 9101, "Zeppelin zeppelin: the zeppelin race begins.")
        cls.mentions = show(9102, "Balloon Court", vote_average=9.0)
        episode(
            cls.mentions,
            9102,
            "A long day at the court ends when a distant zeppelin drifts past "
            "the window and everyone argues about lunch instead of the case.",
        )
        # Branch weights are untouched: a title match still wins the page.
        cls.titled = show(9103, "Zeppelin", vote_average=2.0)
        episode(cls.titled, 9103, "Nothing relevant happens.")

        # The prefix trap pair: "murder" must reach "murderer", "war" must
        # never reach "toward" or "warm".
        cls.murders = show(9104, "Quiet Village", vote_average=6.0)
        episode(cls.murders, 9104, "The murderer confesses to both murders.")
        # "warm" is deliberately absent: a word-start prefix is supposed to
        # match (ADR-12: "break" finds Breaking Bad), so \bwar reaches
        # "warm" too. Mid-word is what must stay excluded.
        cls.decoy = show(9105, "Quiet Regards", vote_average=6.0)
        episode(cls.decoy, 9105, "She walks toward the sea, onward.")

    def old_regex_branch(self, term):
        return set(
            Show.objects.filter(_word("seasons__episodes__overview", term)).values_list(
                "id", flat=True
            )
        )

    def test_prefix_matches_what_the_regex_matched(self):
        # The drop-in guarantee: same shows, term by term, including the
        # prefix hits exact matching would silently lose.
        for term in ["murder", "murderer", "war", "zeppelin", "the"]:
            self.assertEqual(set(_episode_fts(term)), self.old_regex_branch(term), term)

    def test_substring_noise_stays_excluded(self):
        # "war" inside "toward" was the garbage that made ADR-12 anchor on
        # word boundaries. Tokens anchor the same way.
        self.assertNotIn(self.decoy.id, _episode_fts("war"))

    def test_bm25_orders_within_the_episode_bucket(self):
        results, _ = run_search("zeppelin")
        names = [s.name for s in results]
        self.assertLess(
            names.index(self.about.name),
            names.index(self.mentions.name),
            "the show about zeppelins must beat the higher-voted mention",
        )

    def test_a_title_hit_still_outranks_the_best_synopsis(self):
        # bm25 must not re-weight branches against each other (#29, ADR-12).
        results, _ = run_search("zeppelin")
        self.assertEqual(results[0].name, self.titled.name)

    def test_the_episode_operator_keeps_bm25_order(self):
        results, _ = run_search("episode:zeppelin")
        names = [s.name for s in results]
        self.assertLess(names.index(self.about.name), names.index(self.mentions.name))


class EpisodeFtsTriggerTests(TestCase):
    """Freezes #29's sync decision: triggers, live at write time.

    The index must answer for the episodes table as it is now, not as it was
    at the last rebuild. Insert, update and delete each have a trigger, and
    the update trigger also fires on season_id because TMDb moves episodes
    between seasons while keeping their id (see Ingestor._upsert_child).
    """

    @classmethod
    def setUpTestData(cls):
        cls.show = Show.objects.create(
            tmdb_id=9201,
            name="Trigger Town",
            slug="trigger-town",
            first_air_date="2012-01-01",
            vote_count=100,
            original_language="en",
            status="Ended",
        )
        cls.season = Season.objects.create(show=cls.show, tmdb_id=9201, season_number=1)

    def test_an_inserted_episode_is_searchable_at_once(self):
        Episode.objects.create(
            season=self.season, tmdb_id=9301, episode_number=1, overview="A quokka smiles."
        )
        self.assertIn(self.show.id, _episode_fts("quokka"))

    def test_an_updated_overview_answers_for_its_new_text_only(self):
        ep = Episode.objects.create(
            season=self.season, tmdb_id=9302, episode_number=2, overview="A quokka smiles."
        )
        ep.overview = "A wombat digs."
        ep.save()
        self.assertIn(self.show.id, _episode_fts("wombat"))
        self.assertNotIn(self.show.id, _episode_fts("quokka"))

    def test_a_deleted_episode_is_gone_from_the_index(self):
        ep = Episode.objects.create(
            season=self.season, tmdb_id=9303, episode_number=3, overview="A quokka smiles."
        )
        ep.delete()
        self.assertEqual(_episode_fts("quokka"), {})

    def test_a_cascade_delete_reaches_the_index(self):
        # Deleting a show cascades through seasons to episodes as row
        # deletes, and each one must fire the delete trigger.
        other = Show.objects.create(
            tmdb_id=9202,
            name="Doomed",
            slug="doomed",
            first_air_date="2012-01-01",
            vote_count=100,
            original_language="en",
            status="Ended",
        )
        season = Season.objects.create(show=other, tmdb_id=9202, season_number=1)
        Episode.objects.create(
            season=season, tmdb_id=9304, episode_number=1, overview="An axolotl waits."
        )
        other.delete()
        self.assertEqual(_episode_fts("axolotl"), {})

    def test_an_episode_moved_between_seasons_answers_for_its_new_show(self):
        # The season move TMDb actually performs: same episode id, new season.
        # An overview-only update trigger would leave it answering for the
        # old show.
        other = Show.objects.create(
            tmdb_id=9203,
            name="New Home",
            slug="new-home",
            first_air_date="2012-01-01",
            vote_count=100,
            original_language="en",
            status="Ended",
        )
        new_season = Season.objects.create(show=other, tmdb_id=9203, season_number=1)
        ep = Episode.objects.create(
            season=self.season, tmdb_id=9305, episode_number=4, overview="A quokka smiles."
        )
        ep.season = new_season
        ep.save()
        found = _episode_fts("quokka")
        self.assertIn(other.id, found)
        self.assertNotIn(self.show.id, found)
