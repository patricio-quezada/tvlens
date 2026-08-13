"""Tests for the recommender's decided invariants.

Each test freezes one decision recorded on QUE-9 (and the 2026-08-06 review),
so a later layer that reweights this edge, or an ingest change that shifts the
data, fails loudly here instead of silently reranking the catalog.
"""

from django.test import TestCase
from django.urls import reverse

from .models import CastMember, CrewMember, Person, Show
from .recommenders import (
    name_connections,
    role_index,
    shared_connections,
    similar_by_people,
)


class SimilarByPeopleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Show.objects.create(tmdb_id=1, name="A", number_of_episodes=10)
        cls.b = Show.objects.create(tmdb_id=2, name="B", number_of_episodes=10)
        cls.p = Person.objects.create(tmdb_id=1, name="Lead")

    def test_min_rule_takes_weaker_end(self):
        CastMember.objects.create(show=self.a, person=self.p, episode_count=10)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=2)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 0.2)

    def test_person_counted_once_at_best_count_across_cast_and_crew(self):
        CastMember.objects.create(show=self.a, person=self.p, episode_count=4)
        CrewMember.objects.create(
            show=self.a, person=self.p, job="Director", episode_count=10
        )
        CastMember.objects.create(show=self.b, person=self.p, episode_count=10)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 1.0)
        self.assertEqual(r.shared_people, 1)

    def test_null_episode_count_is_shared_but_weighs_zero(self):
        CastMember.objects.create(show=self.a, person=self.p, episode_count=None)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=10)
        [r] = similar_by_people(self.a)
        self.assertEqual((r.score, r.shared_people), (0.0, 1))

    def test_service_job_excluded_on_candidate_side(self):
        CrewMember.objects.create(
            show=self.a, person=self.p, job="Writer", episode_count=10
        )
        CrewMember.objects.create(
            show=self.b, person=self.p, job="Casting", episode_count=10
        )
        self.assertEqual(similar_by_people(self.a), [])

    def test_service_job_variant_excluded(self):
        # The 2026-08-06 review found eight casting variants leaking past the
        # original list. Freeze one of them.
        CrewMember.objects.create(
            show=self.a, person=self.p, job="Writer", episode_count=10
        )
        CrewMember.objects.create(
            show=self.b, person=self.p, job="Extras Casting", episode_count=10
        )
        self.assertEqual(similar_by_people(self.a), [])

    def test_ratio_caps_at_one(self):
        # Credit rollups drift ahead of the episode total on returning series
        # (595 credited episodes of a 594-episode run). Nobody made more than
        # all of a show.
        CastMember.objects.create(show=self.a, person=self.p, episode_count=11)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=12)
        [r] = similar_by_people(self.a)
        self.assertAlmostEqual(r.score, 1.0)

    def test_zero_number_of_episodes_yields_zero_score_not_crash(self):
        self.a.number_of_episodes = 0
        self.a.save()
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=5)
        [r] = similar_by_people(self.a)
        self.assertEqual(r.score, 0.0)

    def test_real_edge_outranks_popular_null_edge(self):
        # A candidate with a genuine episode-weighted edge must beat a more
        # popular candidate whose only link is a series-level (null) credit.
        # Freezes both the zero-weight decision and the popularity tie-break.
        popular = Show.objects.create(
            tmdb_id=3, name="Popular", number_of_episodes=10, popularity=99.0
        )
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.a, person=ghost, episode_count=None)
        CastMember.objects.create(show=popular, person=ghost, episode_count=None)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["B", "Popular"])

    def test_show_with_no_qualifying_people_returns_empty_list(self):
        self.assertEqual(similar_by_people(self.a), [])

    def test_zero_episode_source_estimates_from_candidate_side(self):
        # Revised on QUE-11 (2026-08-07): a zero-episode source still knows
        # half of every edge, the candidate's side, so the list ranks by that
        # estimate. The three candidates are rigged so estimate order (Lead,
        # Half, Cameo), popularity order (Cameo first), and rating order
        # (Cameo first) all disagree; only the estimate explains the result.
        self.a.number_of_episodes = 0
        self.a.save()
        lead = Show.objects.create(
            tmdb_id=3, name="Lead", number_of_episodes=10,
            vote_average=6.0, vote_count=100, popularity=1.0,
        )
        half = Show.objects.create(
            tmdb_id=4, name="Half", number_of_episodes=10,
            vote_average=7.0, vote_count=100, popularity=50.0,
        )
        cameo = Show.objects.create(
            tmdb_id=5, name="Cameo", number_of_episodes=10,
            vote_average=9.9, vote_count=9000, popularity=99.0,
        )
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=lead, person=self.p, episode_count=10)
        CastMember.objects.create(show=half, person=self.p, episode_count=5)
        CastMember.objects.create(show=cameo, person=self.p, episode_count=1)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["Lead", "Half", "Cameo"])
        self.assertEqual(results.mode, "estimated")

    def test_estimate_ties_break_on_rating_then_votes(self):
        # Equal estimates fall to the rating tie-break: vote_average
        # descending, then vote_count so a 10.0 on three votes cannot beat
        # an 8.9 on ten thousand. Popularity would order it Loud, Ties,
        # Grail; the rule orders it Grail, Ties, Loud.
        self.a.number_of_episodes = 0
        self.a.save()
        grail = Show.objects.create(
            tmdb_id=3, name="Grail", number_of_episodes=10,
            vote_average=9.5, vote_count=9000, popularity=1.0,
        )
        ties = Show.objects.create(
            tmdb_id=4, name="Ties", number_of_episodes=10,
            vote_average=8.9, vote_count=10000, popularity=50.0,
        )
        loud = Show.objects.create(
            tmdb_id=5, name="Loud", number_of_episodes=10,
            vote_average=8.9, vote_count=3, popularity=99.0,
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
        # rung ranks by TMDb rating, per the original QUE-11 decision.
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        low = Show.objects.create(
            tmdb_id=3, name="Low", number_of_episodes=10,
            vote_average=6.0, vote_count=100, popularity=99.0,
        )
        high = Show.objects.create(
            tmdb_id=4, name="High", number_of_episodes=10,
            vote_average=9.0, vote_count=100, popularity=1.0,
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
            tmdb_id=3, name="Rated", number_of_episodes=10,
            vote_average=9.9, vote_count=10000, popularity=99.0,
        )
        ghost = Person.objects.create(tmdb_id=2, name="Ghost")
        CastMember.objects.create(show=self.a, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.b, person=self.p, episode_count=5)
        CastMember.objects.create(show=self.a, person=ghost, episode_count=None)
        CastMember.objects.create(show=rated, person=ghost, episode_count=None)
        results = similar_by_people(self.a)
        self.assertEqual([s.name for s in results], ["B", "Rated"])
        self.assertEqual(results.mode, "weighted")


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
        self.assertEqual([a.slug, b.slug, c.slug],
                         ["the-office", "the-office-2", "the-office-3"])

    def test_slug_is_stable_when_name_changes(self):
        s = Show.objects.create(tmdb_id=104, name="Original Name")
        original = s.slug
        s.name = "A Completely Different Name"
        s.save()
        self.assertEqual(s.slug, original)


class SharedConnectionsTests(TestCase):
    """The show-detail page's "why": the shared people, named and ordered by
    the same episode-share that ranked them (QUE-2 wireframe)."""

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(tmdb_id=1, name="Source", number_of_episodes=10)
        cls.cand = Show.objects.create(tmdb_id=2, name="Cand", number_of_episodes=10)
        cls.lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        cls.maker = Person.objects.create(tmdb_id=2, name="The Maker")
        cls.extra = Person.objects.create(tmdb_id=3, name="Bit Player")

    def _connections(self):
        return shared_connections(
            self.source, role_index(self.source),
            self.cand, role_index(self.cand),
        )

    def test_contribution_orders_edges_and_count_matches_recommender(self):
        # Lead is in all of both (1.0); the extra shares one episode (0.1).
        CastMember.objects.create(show=self.source, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        CastMember.objects.create(show=self.cand, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        CastMember.objects.create(show=self.source, person=self.extra, order=600,
                                  character="Waiter", episode_count=1)
        CastMember.objects.create(show=self.cand, person=self.extra, order=600,
                                  character="Waiter", episode_count=1)
        conns = self._connections()
        self.assertEqual([c.name for c in conns], ["Lead Actor", "Bit Player"])
        self.assertAlmostEqual(conns[0].contribution, 1.0)
        # len equals the recommender's shared_people, both dedupe by person.
        [ranked] = similar_by_people(self.source)
        self.assertEqual(len(conns), ranked.shared_people)

    def test_named_leads_with_cast_names_marquee_and_counts_the_rest(self):
        CastMember.objects.create(show=self.source, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        CastMember.objects.create(show=self.cand, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        CrewMember.objects.create(show=self.source, person=self.maker,
                                  job="Creator", episode_count=10)
        CrewMember.objects.create(show=self.cand, person=self.maker,
                                  job="Creator", episode_count=10)
        CastMember.objects.create(show=self.source, person=self.extra, order=600,
                                  character="Waiter", episode_count=1)
        CastMember.objects.create(show=self.cand, person=self.extra, order=600,
                                  character="Waiter", episode_count=1)
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
        CastMember.objects.create(show=self.source, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        CrewMember.objects.create(show=self.source, person=self.lead,
                                  job="Director", episode_count=2)
        CastMember.objects.create(show=self.cand, person=self.lead, order=0,
                                  character="Hero", episode_count=10)
        [c] = self._connections()
        self.assertEqual((c.kind, c.role), ("cast", "Hero"))

    def test_falls_back_to_strongest_edges_when_nothing_is_prominent(self):
        # Only a shared bit player: no recognizable cast, no marquee crew.
        # The callout still names someone rather than a bare count.
        CastMember.objects.create(show=self.source, person=self.extra, order=600,
                                  character="Waiter", episode_count=5)
        CastMember.objects.create(show=self.cand, person=self.extra, order=600,
                                  character="Waiter", episode_count=5)
        named, others = name_connections(self._connections())
        self.assertEqual([c.name for c in named], ["Bit Player"])
        self.assertEqual(others, 0)


class ShowDetailViewTests(TestCase):
    """The route, the 404, and that the page surfaces the named connection."""

    @classmethod
    def setUpTestData(cls):
        cls.source = Show.objects.create(
            tmdb_id=1, name="Source Show", number_of_episodes=10,
            tagline="A tagline.", vote_average=8.9, vote_count=1234,
        )
        cls.cand = Show.objects.create(tmdb_id=2, name="Candidate Show",
                                       number_of_episodes=10)
        lead = Person.objects.create(tmdb_id=1, name="Jane Star")
        CastMember.objects.create(show=cls.source, person=lead, order=0,
                                  character="The Detective", episode_count=10)
        CastMember.objects.create(show=cls.cand, person=lead, order=0,
                                  character="The Detective", episode_count=10)

    def test_detail_page_renders_show_and_named_connection(self):
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Source Show", body)
        self.assertIn("A tagline.", body)
        self.assertIn("1,234 votes", body)
        self.assertIn("Ranked by shared cast and crew.", body)
        self.assertIn("Candidate Show", body)
        self.assertIn("Jane Star", body)
        self.assertIn("The Detective", body)

    def test_detail_url_uses_slug(self):
        self.assertEqual(self.source.get_absolute_url(), "/shows/source-show/")

    def test_unknown_slug_returns_404(self):
        self.assertEqual(
            self.client.get(reverse("shows:detail", args=["nope"])).status_code, 404
        )
