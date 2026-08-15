"""Tests for the recommender's decided invariants.

Each test freezes one decision recorded on QUE-9 (and the 2026-08-06 review),
so a later layer that reweights this edge, or an ingest change that shifts the
data, fails loudly here instead of silently reranking the catalog.
"""

from django.test import TestCase
from django.urls import reverse

from .models import CastMember, CrewMember, Person, Show
from .recommenders import (
    compose_callout,
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

    def test_name_by_score_ranks_marquee_ahead_of_lower_scoring_cast(self):
        # A composer on every episode of both (1.0) outscores a recognizable
        # lead who only guested (0.2). Name-by-score names the composer first,
        # cast and crew merged in one order, not the cast first by prominence.
        CastMember.objects.create(show=self.source, person=self.lead, order=0,
                                  character="Hero", episode_count=2)
        CastMember.objects.create(show=self.cand, person=self.lead, order=0,
                                  character="Hero", episode_count=2)
        CrewMember.objects.create(show=self.source, person=self.maker,
                                  job="Original Music Composer", episode_count=10)
        CrewMember.objects.create(show=self.cand, person=self.maker,
                                  job="Original Music Composer", episode_count=10)
        named, others = name_connections(self._connections())
        self.assertEqual([c.name for c in named], ["The Maker", "Lead Actor"])
        self.assertGreater(named[0].contribution, named[1].contribution)


class CalloutProseTests(TestCase):
    """The 7a callout: one flowing sentence per recommendation, with a
    data-driven lead, honest source-side episode context, roles in prose, and
    the long tail collapsed into a count (QUE-2 wireframe)."""

    def setUp(self):
        # A short source and candidate so 'every episode' is easy to trigger.
        self.source = Show.objects.create(tmdb_id=1, name="Source",
                                          number_of_episodes=62)
        self.cand = Show.objects.create(tmdb_id=2, name="Cand",
                                        number_of_episodes=63)
        self._pid = 10

    def _person(self, name):
        self._pid += 1
        return Person.objects.create(tmdb_id=self._pid, name=name)

    def _cast(self, show, person, order, character, eps):
        CastMember.objects.create(show=show, person=person, order=order,
                                  character=character, episode_count=eps)

    def _crew(self, show, person, job, eps):
        CrewMember.objects.create(show=show, person=person, job=job,
                                  episode_count=eps)

    def _callout(self):
        conns = shared_connections(
            self.source, role_index(self.source),
            self.cand, role_index(self.cand),
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
        self.assertIn("plays Jesse Pinkman in all 62 episodes",
                      self._text(self._callout()))

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

    def test_creator_led_lead_phrase(self):
        maker = self._person("The Creator")
        self._crew(self.source, maker, "Creator", 62)
        self._crew(self.cand, maker, "Creator", 63)
        self.assertEqual(self._callout()["lead"], "Made by the same people:")

    def test_all_cast_lead_counts_the_actors(self):
        # Two shared actors, both strong ties, nothing else: the actors are the
        # whole story.
        a = self._person("Actor One")
        b = self._person("Actor Two")
        self._cast(self.source, a, 0, "Hero", 62)
        self._cast(self.cand, a, 0, "Hero", 63)
        self._cast(self.source, b, 1, "Sidekick", 60)
        self._cast(self.cand, b, 1, "Sidekick", 60)
        self.assertEqual(self._callout()["lead"], "Two actors carry over:")

    def test_thin_thread_lead_when_strongest_edge_is_light(self):
        # A single guest across a few episodes of a long run: a thin thread.
        guest = self._person("A Guest")
        self._cast(self.source, guest, 0, "Waiter", 3)
        self._cast(self.cand, guest, 0, "Waiter", 3)
        self.assertEqual(self._callout()["lead"], "A thinner thread:")

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
