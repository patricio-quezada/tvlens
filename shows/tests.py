"""Tests for the recommender's decided invariants.

Each test freezes one decision recorded on QUE-9 (and the 2026-08-06 review),
so a later layer that reweights this edge, or an ingest change that shifts the
data, fails loudly here instead of silently reranking the catalog.
"""

from django.test import TestCase

from .models import CastMember, CrewMember, Person, Show
from .recommenders import similar_by_people


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
