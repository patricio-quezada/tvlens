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

from .models import (
    CastMember,
    CrewMember,
    Episode,
    Genre,
    Person,
    Rating,
    Season,
    Show,
    SimilarShow,
    WatchHistory,
)
from .personalization import (
    TOP_PICK_FLOOR,
    rated_shows,
    SIDE_QUEST_CENTRALITY_EXPONENT,
    SIDE_QUEST_HOP_DECAY,
    SIDE_QUEST_SEED_FLOOR,
    build_profile,
    rerank,
    side_quests,
    top_picks,
)
from .views import DETAIL_RECOMMENDATION_LIMIT
from .recommenders import (
    SQLITE_MAX_VARS_SAFE,
    RankedShows,
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
        # Revised in ADR-05 (2026-08-07): a zero-episode source still knows
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
        # rung ranks by TMDb rating, per the original ADR-05 decision.
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
    the long tail collapsed into a count (issue #2 wireframe)."""

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
        self.assertEqual(
            Rating.objects.get(user=self.alice, show=self.show).score, 4.0
        )

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
            tmdb_id=1, name="G", number_of_episodes=10,
            vote_average=9.0, popularity=1.0,
        )
        g.genres.add(good)
        b = Show.objects.create(
            tmdb_id=2, name="B", number_of_episodes=10,
            vote_average=6.0, popularity=999.0,
        )
        b.genres.add(weak)
        profile = build_profile(AnonymousUser())
        self.assertTrue(profile.is_cold_start)
        self.assertGreater(profile.genre_weights[good.id], 0)
        self.assertLess(profile.genre_weights[weak.id], 0)
        self.assertGreater(
            profile.genre_weights[good.id], profile.genre_weights[weak.id]
        )


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
            tmdb_id=1, name="Alpha", number_of_episodes=10,
            vote_average=8.0, popularity=3.0,
        )
        cls.a.genres.add(cls.drama)
        cls.b = Show.objects.create(
            tmdb_id=2, name="Bravo", number_of_episodes=10,
            vote_average=8.0, popularity=2.0,
        )
        cls.b.genres.add(cls.comedy)
        cls.c = Show.objects.create(
            tmdb_id=3, name="Charlie", number_of_episodes=10,
            vote_average=8.0, popularity=1.0,
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
            tmdb_id=2, name="DramaPick", number_of_episodes=10,
            vote_average=8.0, popularity=5.0,
        )
        cls.drama_pick.genres.add(drama)
        cls.comedy_pick = Show.objects.create(
            tmdb_id=3, name="ComedyPick", number_of_episodes=10,
            vote_average=8.0, popularity=1.0,
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
        self.assertLess(body.index("ComedyPick"), body.index("DramaPick"))
        self.assertIn("reordered for fan", body)


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
                tmdb_id=tmdb_id, name=name, number_of_episodes=10,
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
                source=source, target=target, rank=rank, score=score,
                shared_people=1, mode="weighted",
            )

        edge(cls.alpha, cls.strong_partial, 0, 1.8)   # big edge, one new genre
        edge(cls.alpha, cls.fully_new, 1, 0.8)        # smaller edge, all new
        edge(cls.alpha, cls.delta, 2, 0.3)            # new, until it is watched
        edge(cls.bravo, cls.same_only, 0, 5.0)        # the graph's best edge
        edge(cls.charlie, cls.weak_new, 4, 0.05)      # all new, barely an edge
        edge(cls.charlie, cls.deep_new, 7, 2.0)       # all new, but rank 7
        edge(cls.outsider, cls.bystander, 0, 9.0)     # nothing to do with us

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
            "lukewarm", self.alpha, self.bravo, self.charlie,
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
        self.assertEqual(self._names(quests)[:2], ["FullyNew", "StrongPartial"])
        self.assertGreater(quests[1].quest_score, quests[0].quest_score)

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
        self.assertEqual(
            sorted(g.name for g in quest.quest_new_genres), ["SciFi", "Western"]
        )

    def test_limit_takes_the_most_surprising(self):
        quests = side_quests(self._rater(), limit=2)
        self.assertEqual(self._names(quests), ["FullyNew", "StrongPartial"])

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
            s = Show.objects.create(
                tmdb_id=tmdb_id, name=name, number_of_episodes=10
            )
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
                source=source, target=target, rank=rank, score=score,
                shared_people=1, mode="weighted",
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
        self.assertLess(
            self._rank_of(quests, "Modest"), self._rank_of(quests, "Blockbuster")
        )

    def test_strength_still_counts_for_something(self):
        # Compression is not erasure: between two fully novel shows, the one on
        # the better edge still wins. NearNew (3.0) outranks Modest (0.8), and
        # both are 100% new.
        quests = side_quests(self._rater("still"))
        self.assertLess(
            self._rank_of(quests, "NearNew"), self._rank_of(quests, "Modest")
        )

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
        self.assertLess(
            self._rank_of(quests, "NearNew"), self._rank_of(quests, "FarNew")
        )

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
        self.assertEqual(
            next(s for s in quests if s.name == "Everyone").quest_reach, 3
        )
        self.assertEqual(
            next(s for s in quests if s.name == "OnlyOne").quest_reach, 1
        )
        self.assertLess(
            self._rank_of(quests, "OnlyOne"), self._rank_of(quests, "Everyone")
        )

    # ── the arithmetic, stated once ─────────────────────────────────────────

    def test_surprise_is_strength_times_novelty_times_centrality(self):
        # One pick, spelled out, so the formula is readable in one place.
        quests = side_quests(self._rater("math"))
        pick = next(s for s in quests if s.name == "Everyone")
        expected = math.log1p(2.0) * 1.0 * (3 ** -SIDE_QUEST_CENTRALITY_EXPONENT)
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
            tmdb_id=1, name="Rated Show", number_of_episodes=10,
            vote_average=8.4, vote_count=1200,
        )
        cls.unrated = Show.objects.create(
            tmdb_id=2, name="Unrated Show", number_of_episodes=10,
            vote_average=7.0, vote_count=500,
        )
        cls.unvoted = Show.objects.create(
            tmdb_id=3, name="Unvoted Show", number_of_episodes=10,
            vote_average=0, vote_count=0,
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
            tmdb_id=1, name="Loved Show", number_of_episodes=10,
            vote_average=6.0, vote_count=500,
        )
        cls.hated = Show.objects.create(
            tmdb_id=2, name="Hated Show", number_of_episodes=10,
            vote_average=9.0, vote_count=500,
        )
        cls.theirs = Show.objects.create(
            tmdb_id=3, name="Someone Elses Show", number_of_episodes=10,
            vote_average=8.0, vote_count=500,
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
        self.client.post(
            reverse("shows:rate", args=[self.loved.slug]), {"score": "4.5"}
        )
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
                tmdb_id=10 + i, name=f"Drama {i}", number_of_episodes=10,
                vote_average=8.0 - i, vote_count=100,
            )
            s.genres.set([cls.drama])
            setattr(cls, f"drama{i}", s)
        c = Show.objects.create(
            tmdb_id=20, name="A Comedy", number_of_episodes=10,
            vote_average=7.0, vote_count=100,
        )
        c.genres.set([cls.comedy])
        cls.a_comedy = c

    def test_the_genre_page_is_a_grid_not_a_horizontal_scroller(self):
        body = self.client.get(
            reverse("shows:genre", args=[self.drama.id])
        ).content.decode()
        self.assertIn('class="grid"', body)
        # The element, not the word: base.html carries .row-scroller CSS on
        # every page, so a bare substring check would always match.
        self.assertNotIn('class="row-scroller"', body)

    def test_the_genre_page_shows_only_that_genre(self):
        body = self.client.get(
            reverse("shows:genre", args=[self.drama.id])
        ).content.decode()
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

        Rating.objects.create(user=self.user, show=self.drama0, score=4.5)
        now = self.client.get(reverse("shows:index")).context["favorite_genre_ids"]
        self.assertEqual(set(now), {self.drama.id})

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
        cls.source = Show.objects.create(
            tmdb_id=1, name="Source", number_of_episodes=10
        )
        # One person shared with every candidate, so Layer 1 connects all of
        # them and the store fills past the display limit.
        person = Person.objects.create(tmdb_id=1, name="Shared Lead")
        CastMember.objects.create(
            show=cls.source, person=person, order=0,
            character="Lead", episode_count=10,
        )
        for i in range(10):
            s = Show.objects.create(
                tmdb_id=100 + i, name=f"Candidate {i}", number_of_episodes=10,
            )
            CastMember.objects.create(
                show=s, person=person, order=0, character="Lead", episode_count=10,
            )
        call_command("rebuild_similar_shows", stdout=StringIO())

    def test_the_show_page_offers_a_bounded_number_of_recommendations(self):
        # ADR-07 stores 12 edges per show; the page used to render every one of
        # them with its own prose callout (#16, item 6).
        resp = self.client.get(self.source.get_absolute_url())
        self.assertEqual(
            len(resp.context["recommendations"]), DETAIL_RECOMMENDATION_LIMIT
        )
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
                tmdb_id=tmdb_id, name=name, number_of_episodes=10,
                vote_average=vote, vote_count=500, popularity=popularity,
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
            g.name for g in Genre.objects.annotate(
                p=Max("shows__popularity")
            ).order_by("-p")
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
