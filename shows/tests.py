"""Tests for the recommender's decided invariants.

Each test freezes one decision recorded on QUE-9 (and the 2026-08-06 review),
so a later layer that reweights this edge, or an ingest change that shifts the
data, fails loudly here instead of silently reranking the catalog.
"""

from io import StringIO

from django.contrib.auth.models import AnonymousUser, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import (
    CastMember,
    CrewMember,
    Episode,
    Person,
    Rating,
    Season,
    Show,
    SimilarShow,
    WatchHistory,
)
from .recommenders import (
    SQLITE_MAX_VARS_SAFE,
    compose_callout,
    name_connections,
    role_index,
    shared_connections,
    similar_by_cast,
    similar_by_crew,
    similar_by_people,
    stored_similar,
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

    def test_no_lead_phrase_in_callout(self):
        # Editorial leads are gone (decided 2026-08-14): the callout carries no
        # 'lead' key and never prefixes a header, whatever the connection profile.
        maker = self._person("The Creator")   # would once have led "Made by..."
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
        cls.b = Show.objects.create(
            tmdb_id=2, name="Bshow", number_of_episodes=10, popularity=5.0
        )
        cls.c = Show.objects.create(
            tmdb_id=3, name="Cshow", number_of_episodes=10, popularity=1.0
        )
        cls.lonely = Show.objects.create(
            tmdb_id=9, name="Lonely", number_of_episodes=10
        )
        lead = Person.objects.create(tmdb_id=1, name="Lead Actor")
        side = Person.objects.create(tmdb_id=2, name="Side Player")
        hermit = Person.objects.create(tmdb_id=3, name="Hermit")
        # Lead: all of Src and all of B -> strong edge (1.0).
        CastMember.objects.create(show=cls.src, person=lead, order=0,
                                  character="Hero", episode_count=10)
        CastMember.objects.create(show=cls.b, person=lead, order=0,
                                  character="Hero", episode_count=10)
        # Side: all of Src, two episodes of C -> weaker edge (0.2).
        CastMember.objects.create(show=cls.src, person=side, order=1,
                                  character="Rival", episode_count=10)
        CastMember.objects.create(show=cls.c, person=side, order=1,
                                  character="Rival", episode_count=2)
        CastMember.objects.create(show=cls.lonely, person=hermit, order=0,
                                  character="Alone", episode_count=10)

        # A zero-episode source ranks by the candidate side: mode "estimated".
        cls.blank = Show.objects.create(
            tmdb_id=4, name="Blank", number_of_episodes=0
        )
        traveler = Person.objects.create(tmdb_id=4, name="Traveler")
        CastMember.objects.create(show=cls.blank, person=traveler, order=0,
                                  character="Wanderer", episode_count=5)
        CastMember.objects.create(show=cls.b, person=traveler, order=2,
                                  character="Wanderer", episode_count=5)

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
        self.assertAlmostEqual(stored[1].score, 0.2)
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
        modes = set(
            SimilarShow.objects.filter(source=self.blank).values_list(
                "mode", flat=True
            )
        )
        self.assertEqual(modes, {"estimated"})

    def test_detail_page_renders_from_the_stored_edges(self):
        # The detail view now reads stored_similar. With the store built, the
        # page must surface Src's top candidate and its shared-people count.
        resp = self.client.get(self.src.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Bshow", body)
        self.assertIn("Lead Actor", body)
        self.assertIn("Ranked by shared cast and crew.", body)

    def test_rebuild_is_wholesale_replacing_stale_edges(self):
        # A stale edge left by a prior build must not survive the next rebuild.
        SimilarShow.objects.create(
            source=self.lonely, target=self.src, rank=0, score=9.9,
            shared_people=1, mode="weighted",
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
        cls.src = Show.objects.create(
            tmdb_id=1, name="Src", number_of_episodes=100
        )
        cls.cand = Show.objects.create(
            tmdb_id=2, name="Cand", number_of_episodes=100
        )
        Person.objects.bulk_create(
            [Person(tmdb_id=1000 + i, name=f"P{i}") for i in range(n)]
        )
        people = list(Person.objects.all())
        CastMember.objects.bulk_create(
            [CastMember(show=cls.src, person=p, episode_count=100) for p in people]
        )
        CastMember.objects.bulk_create(
            [CastMember(show=cls.cand, person=p, episode_count=100) for p in people]
        )
        CrewMember.objects.bulk_create(
            [
                CrewMember(show=cls.src, person=p, job="Writer", episode_count=100)
                for p in people
            ]
        )
        CrewMember.objects.bulk_create(
            [
                CrewMember(show=cls.cand, person=p, job="Writer", episode_count=100)
                for p in people
            ]
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
        cls.show = Show.objects.create(
            tmdb_id=1, name="Rated Show", number_of_episodes=10
        )
        cls.other = Show.objects.create(
            tmdb_id=2, name="Other Show", number_of_episodes=10
        )
        cls.alice = User.objects.create_user("alice", password="pw-alice-123")
        cls.bob = User.objects.create_user("bob", password="pw-bob-123")

    def _rate(self, show, score):
        return self.client.post(
            reverse("shows:rate", args=[show.slug]), {"score": score}
        )

    def test_recording_a_rating_persists_the_score(self):
        self.client.force_login(self.alice)
        resp = self._rate(self.show, "3.5")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Rating.objects.get(user=self.alice, show=self.show).score, 3.5
        )

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
        # 3.5 is the fourth input in the high-to-low widget (5.0, 4.5, 4.0, 3.5).
        self.assertIn('value="3.5" id="star-4" checked', body)
        self.assertIn("Your rating: 3.5", body)

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
        cls.rated = Show.objects.create(
            tmdb_id=1, name="Rated Only", number_of_episodes=10
        )
        cls.watched = Show.objects.create(
            tmdb_id=2, name="Watched Only", number_of_episodes=10
        )
        cls.untouched = Show.objects.create(
            tmdb_id=3, name="Untouched", number_of_episodes=10
        )

        # Rated but never played: the rating alone implies watched.
        Rating.objects.create(user=cls.user, show=cls.rated, score=4.0)

        # Played but never rated, across two episodes so a missing distinct()
        # would surface the watched show twice in the bulk queryset.
        season = Season.objects.create(
            show=cls.watched, tmdb_id=100, season_number=1
        )
        for i in (1, 2):
            ep = Episode.objects.create(
                season=season, tmdb_id=1000 + i, episode_number=i
            )
            WatchHistory.objects.create(user=cls.user, episode=ep)

    def test_rated_show_counts_as_watched_without_watch_history(self):
        self.assertTrue(self.rated.is_watched_by(self.user))

    def test_watch_history_only_show_counts_as_watched(self):
        self.assertTrue(self.watched.is_watched_by(self.user))

    def test_untouched_show_is_not_watched(self):
        self.assertFalse(self.untouched.is_watched_by(self.user))

    def test_watched_by_returns_both_signals_once_each(self):
        watched_pks = sorted(
            Show.objects.watched_by(self.user).values_list("pk", flat=True)
        )
        self.assertEqual(watched_pks, sorted([self.rated.pk, self.watched.pk]))

    def test_anonymous_user_has_watched_nothing(self):
        anon = AnonymousUser()
        self.assertFalse(self.rated.is_watched_by(anon))
        self.assertEqual(list(Show.objects.watched_by(anon)), [])
