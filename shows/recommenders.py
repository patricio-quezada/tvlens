"""Layer 1 of the TVLens recommender: the static content graph.

Shows connect to each other through what they share. The first edge is cast.
Two shows that share several actors are related in a way that popularity
rankings and genre labels cannot see.

The second edge is crew, which is denser than cast and catches franchise
structure that cast cannot. It needs one correction: not every job on a
production says something about the show. See SERVICE_JOBS below.

The merged edge weights every shared person by episode share raised to
INVOLVEMENT_EXPONENT, so a lead who carried a whole run outranks a crowd of
one-episode guests instead of being buried by it. See similar_by_people and
docs/adr/04-episode-weighted-people-recommender.md for the decisions behind
it, and INVOLVEMENT_EXPONENT below for why the weight is not linear.

Later layers stack genre and learned weights on top of these edges. Layer 2
re-ranks this list per user rather than scoring shows a second time; see
docs/adr/08-layer2-personalized-reranking.md.
"""

from collections import namedtuple
from itertools import batched

from django.db.models import Count, Q

from .models import CastMember, CrewMember, Show, SimilarShow

# How much of a show a person actually is, before their share is summed. Each
# shared person contributes min(share_A, share_B) ** this, so involvement is
# worth more than headcount: carrying a whole run should outweigh appearing
# once, and 300 one-episode guests should not outrank a co-lead. Above 1 and
# continuous, so it reweights rather than cutting: ADR-04's rejected
# alternatives ruled out a hard cutoff, which "creates a cliff, throws away
# thin shows, and hurts short-form content", and a curve does none of that.
# Same shape and same argument as SIDE_QUEST_CENTRALITY_EXPONENT in
# personalization.py.
#
# 1.375 is measured, not chosen by taste (ADR-04, amended 2026-08-26). The
# defect it fixes: 217,622 of 278,632 cast rows carry episode_count = 1, so
# under a linear sum 300 guests at 0.01 each beat a full-run co-lead at 1.0.
# The acceptance case is 300 guests at one episode of a hundred against one
# co-lead: break-even is 1.24, so 1.25 clears it by 1.05x and 1.375 by 1.87x.
# Going higher is worse, not safer. Past about 1.4 the score collapses toward
# whoever has the single strongest tie and the mid-strength tail stops
# carrying register: at 1.5 Silent Witness recommends Loki, The Pitt loses ER,
# and Pokemon loses Pokemon Horizons. 1.375 is the top of that plateau.
INVOLVEMENT_EXPONENT = 1.375

# Jobs that connect two productions without connecting the two shows.
#
# A casting office works across an entire studio slate, so counting it makes
# unrelated shows look related. On the 100-show catalog it linked The Boys to
# Grey's Anatomy on four shared people, every one of them a casting director.
# Excluding these costs two shows of coverage and removes thirteen false pairs.
# See docs/adr/01-exclude-casting-roles.md.
#
# Exact titles by choice (2026-08-06 review): the list is auditable, but a new
# TMDb import can mint a variant that slips past it. When touching ingest,
# re-run: CrewMember.objects.filter(job__icontains="casting")
#             .exclude(job__in=SERVICE_JOBS)
# and add whatever it finds. Every catalog job containing "casting" so far IS
# a casting service.
SERVICE_JOBS = [
    "Casting",
    "Original Casting",
    "Casting Associate",
    "Casting Assistant",
    "Casting Director",
    "Local Casting",
    "Voice Casting",
    # Variants found leaking in the 2026-08-06 review, 45 rows:
    "Additional Casting",
    "Background Casting Director",
    "Casting Coordinator",
    "Casting Researcher",
    "Extras Casting",
    "Extras Casting Assistant",
    "Extras Casting Coordinator",
    "Location Casting",
    # Widened 2026-08-26 for the involvement exponent (ADR-01 amended, ADR-04
    # amended). The exponent below makes a single full-run credit decisive, and
    # that surfaced a class of credit ADR-01's argument already covered but its
    # list did not reach: a facility or a vendor contracted per production. On
    # the 464-show catalog, 121 of the 525 show pairs sharing someone at
    # episode-share >= 0.9 were one of these, and under a linear score they were
    # invisible because one person's 1.0 drowned in the crowd. Costs 1,985 crew
    # credits (3.5%), 1,820 person-show links (0.6%), and no show's coverage.
    #
    # Sound post: a sound house books across a studio slate the way a casting
    # office does. One sound director tied The Apothecary Diaries to Frieren,
    # SPY x FAMILY and Isekai Office Worker; a re-recording mixer tied The
    # Mentalist to Lanterns and The Pitt.
    "Sound",
    "Sound mixer",
    "Sound Mixer",
    "Sound Re-Recording Mixer",
    "Additional Sound Re-Recording Mixer",
    "Sound Re-Recording Assistant",
    "Additional Sound Re-Recordist",
    "Production Sound Mixer",
    "Sound Recordist",
    "Sound Director",
    "Sound Supervisor",
    "Supervising Sound Editor",
    "Sound Editor",
    "Assistant Sound Editor",
    "First Assistant Sound Editor",
    "Sound Effects",
    "Sound Effects Editor",
    "Sound Effects Designer",
    "Special Sound Effects",
    "Supervising Sound Effects Editor",
    "Sound Designer",
    "Sound Engineer",
    "Sound Assistant",
    "Sound Mix Technician",
    "Utility Sound",
    "Second Assistant Sound",
    "Boom Operator",
    "Dialogue Editor",
    "Supervising Dialogue Editor",
    "Foley",
    "Foley Artist",
    "Foley Editor",
    "Foley Mixer",
    "Foley Recordist",
    "Foley Supervisor",
    "Scoring Mixer",
    "ADR Supervisor",
    "ADR Editor",
    "Supervising ADR Editor",
    "ADR Mixer",
    "ADR Recordist",
    "ADR Recording Engineer",
    "ADR Coordinator",
    "ADR & Dubbing",
    "Additional Soundtrack",
    # Music SERVICE, never composition. A music supervisor licenses tracks
    # across a slate; a music house produces for whoever books it. Composing is
    # deliberately absent: a score is authorial, MARQUEE_JOBS already treats it
    # as show-defining, and its full-run pairs are mostly right (Berlin and its
    # spinoff, Breaking Bad and Better Call Saul). One music supervisor is why
    # Silo ranked second on Breaking Bad ahead of Bryan Cranston's own show.
    "Music Supervisor",
    "Assistant Music Supervisor",
    "Music Producer",
    "Executive Music Producer",
    "Music Score Producer",
    "Music Coordinator",
    "Music Editor",
    "Supervising Music Editor",
    "Music Consultant",
    # Picture finishing: the post house that finishes the picture, booked per
    # production. A colorist working the full run of both put Marvel's
    # Daredevil first on Elementary.
    "Colorist",
    "Additional Colorist",
    "Senior Colorist",
    "Online Editor",
    "Digital Intermediate Editor",
    "Digital Intermediate Producer",
    "Post Production Supervisor",
    "Post Production Coordinator",
    "Post Production Assistant",
    "Post-Production Manager",
    "Post Production Consulting",
    "Post Producer",
    "Post Production Producer",
    "Finishing Producer",
    "Executive In Charge Of Post Production",
    # Vendors and on-set services hired by the production, not part of the
    # show. A marine coordinator credited on all 110 episodes of Miami Vice and
    # a writer credited on all 740 of Real Time with Bill Maher made Bill Maher
    # Miami Vice's top recommendation.
    "Driver",
    "Production Driver",
    "Specialized Driver",
    "Transportation Captain",
    "Transportation Co-Captain",
    "Transportation Coordinator",
    "Picture Car Coordinator",
    "Marine Coordinator",
    "Animal Wrangler",
    "Craft Service",
    "Catering",
    "Catering Head Chef",
    "Set Medic",
    "Medical Consultant",
    "Security",
    "Security Coordinator",
]

# Above-the-line crew worth naming next to the cast in a recommendation's
# callout, most show-defining first. A creator or showrunner defines a show; a
# director or composer shapes it. This list decides only who gets NAMED, never
# how a show is scored: scoring is blind to the job (ADR-04). It answers the
# design rule "pitch by cast, and name a marquee creator when one is shared".
MARQUEE_JOBS = [
    "Creator",
    "Showrunner",
    "Executive Producer",
    "Director",
    "Writer",
    "Original Music Composer",
    "Composer",
    "Music",
]

# Billing order below which a cast credit counts as a recognizable name rather
# than a one-scene guest. TMDb bills the featured cast in low positions and
# dumps single-episode guests at 500+, so any cutoff in that gap behaves the
# same; 40 leaves room for a large ensemble's recurring players. Used only to
# decide who to name, never to score.
RECOGNIZABLE_BILLING = 40

# Largest person_id__in batch we bind at once, kept under SQLite's 999-variable
# floor on older builds so a large person set never trips "too many SQL
# variables". See docs/adr/06-sql-variable-ceiling.md.
SQLITE_MAX_VARS_SAFE = 900

# What we know about one person on one show: enough to score them (best_count,
# mirroring similar_by_people) and to name them (role/kind/cast_order). role is
# resolved once per person so a creator reads as a creator and an actor by their
# character. kind is "marquee" (a MARQUEE_JOBS crew role), "cast", or "crew".
RoleInfo = namedtuple("RoleInfo", "name best_count cast_order role kind marquee_rank")

# One shared person tying a candidate back to the source show. contribution is
# the same min(source share, candidate share) the score is built from, so the
# callout orders people by exactly what earned the ranking. The raw counts ride
# along so the prose callout can carry honest episode context ("across 28
# episodes", "scored every episode of both") without a second query.
Connection = namedtuple(
    "Connection",
    "name role kind contribution cast_order marquee_rank "
    "src_count src_episodes cand_count cand_episodes",
)


class RankedShows(list):
    """similar_by_people's return value: shows plus one fact about the order.

    mode names which rung of the fallback ladder ranked the list, so the
    template can caption it honestly. A plain list plus one attribute, so
    callers that expect a list keep working.

        weighted   both sides of every edge known, the normal ranking
        estimated  source side unknown, ranked by the candidate-side share
        rating     no edge carries any signal, ranked by TMDb rating
    """

    def __init__(self, shows=(), mode="weighted"):
        super().__init__(shows)
        self.mode = mode


def similar_by_people(show, limit=12):
    """Return shows ranked by episode-weighted shared people, cast and crew merged.

    The rule, decided in ADR-04 (docs/adr/04-episode-weighted-people-recommender.md):

        score(A, B) = sum over shared people of
            min(episode_count on A / A.number_of_episodes,
                episode_count on B / B.number_of_episodes)
            ** INVOLVEMENT_EXPONENT

    Relative counts make cast and crew the same unit, episode share, so the
    two edges merge without a cast-versus-crew exchange rate. `min` means the
    edge takes the weaker end: a Breaking Bad lead who did three episodes of
    Better Call Saul does not create a strong edge. No floor: every shared
    person contributes their share, however small. The exponent is what stops
    "however small" from adding up to more than a co-lead; it shrinks a thin
    share without ever zeroing it, so nothing is thrown away (ADR-04, amended
    2026-08-26). Service jobs stay excluded on both sides because a casting
    office links productions, not shows, and sixty episodes of casting credit
    does not change that. That list was widened the same day to the facility
    and vendor credits that argument also covers; see SERVICE_JOBS.

    A person can hold several roles on one show, across cast and crew. They
    count once per show pair, at their best episode_count on each show. A null
    episode_count is a series-level credit with no episode rollup, so it
    weighs zero; the person still counts as shared, they just add no score.
    A missing number_of_episodes also yields zero rather than a division
    error, though every show in the catalog has one today. Ratios cap at 1.0:
    credit rollups drift ahead of the episode total on returning series
    (162 rows in the catalog, e.g. 595 credited episodes of a 594-episode
    run), and nobody made more than all of a show.

    Each result carries `score` and `shared_people`. The page sorts by score
    and displays the count. Ties break on popularity, matching the other
    edges. Computed in Python after fetching rows: the source-side ratio does
    not inject cleanly into a single ORM annotation, and at catalog scale a
    materialised edge table is not yet worth its upkeep.

    When every candidate scores 0.0 the weighted order carries no signal.
    That happens when the source show has no episodes recorded yet (TMDb
    "Planned" and "In Production" shows legitimately carry 0) or when every
    shared edge is a null-count series-level credit. Decided in ADR-05
    (docs/adr/05-no-signal-fallback-ladder.md)
    and revised the same day (2026-08-07), the order falls down a ladder, and
    only the order, the candidate set never changes:

        1. weighted   sum of min(source share, candidate share), each raised
                      to INVOLVEMENT_EXPONENT
        2. estimated  the source side is unknowable, so rank by the half we
                      can see: the sum of candidate-side shares, under the
                      same exponent. A candidate whose shared person carried
                      their whole show outranks one where they were a
                      one-episode guest, and that is precisely the exponent's
                      claim, so both rungs have to mean the same thing by
                      "involvement" or they disagree about what they rank.
        3. rating     nothing measurable on any edge, so rank by TMDb rating,
                      vote_average then vote_count so a 10.0 on three votes
                      cannot beat an 8.9 on ten thousand. Rating is a quality
                      signal; popularity is the engagement metric this
                      recommender exists to avoid, and it is never used here.

    Returns a RankedShows list of Show objects, empty when the show has no
    qualifying people recorded.
    """

    def fold_best(best, person_id, episode_count):
        # Null weighs zero but still registers the person as shared. Explicit
        # membership check rather than a sentinel default, so no conceivable
        # count value can keep a shared person out of the dict.
        count = episode_count or 0
        if person_id not in best or count > best[person_id]:
            best[person_id] = count

    own_best = {}
    for person_id, count in CastMember.objects.filter(show=show).values_list(
        "person_id", "episode_count"
    ):
        fold_best(own_best, person_id, count)
    for person_id, count in (
        CrewMember.objects.filter(show=show)
        .exclude(job__in=SERVICE_JOBS)
        .values_list("person_id", "episode_count")
    ):
        fold_best(own_best, person_id, count)
    if not own_best:
        return RankedShows()

    # Unlike the Show-side join in similar_by_crew, these filters run on the
    # credit tables directly, so .exclude() drops casting rows and nothing else.
    #
    # This side keeps the materialised list: scoring needs each person's
    # episode_count in Python, so a subquery does not fit (see
    # docs/adr/06-sql-variable-ceiling.md). Instead the person set is chunked
    # so a decades-long soap's tens of thousands of people never cross SQLite's
    # variable ceiling. fold_best merges across batches, so best_by_show is
    # identical to one unchunked query.
    person_ids = list(own_best)
    best_by_show = {}
    for start in range(0, len(person_ids), SQLITE_MAX_VARS_SAFE):
        batch = person_ids[start : start + SQLITE_MAX_VARS_SAFE]
        for show_id, person_id, count in (
            CastMember.objects.filter(person_id__in=batch)
            .exclude(show=show)
            .values_list("show_id", "person_id", "episode_count")
        ):
            fold_best(best_by_show.setdefault(show_id, {}), person_id, count)
        for show_id, person_id, count in (
            CrewMember.objects.filter(person_id__in=batch)
            .exclude(show=show)
            .exclude(job__in=SERVICE_JOBS)
            .values_list("show_id", "person_id", "episode_count")
        ):
            fold_best(best_by_show.setdefault(show_id, {}), person_id, count)
    if not best_by_show:
        return RankedShows()

    own_episodes = show.number_of_episodes or 0
    results = []
    for other in Show.objects.filter(pk__in=best_by_show):
        other_episodes = other.number_of_episodes or 0
        score = 0.0
        estimate = 0.0
        for person_id, other_count in best_by_show[other.pk].items():
            own_ratio = min(own_best[person_id] / own_episodes, 1.0) if own_episodes else 0.0
            other_ratio = min(other_count / other_episodes, 1.0) if other_episodes else 0.0
            # Zero-guarded because 0.0 ** x is fine but the branch keeps a
            # null-count credit at exactly 0.0 rather than trusting float pow.
            pair = min(own_ratio, other_ratio)
            score += pair**INVOLVEMENT_EXPONENT if pair else 0.0
            estimate += other_ratio**INVOLVEMENT_EXPONENT if other_ratio else 0.0
        other.score = score
        other.estimate = estimate
        other.shared_people = len(best_by_show[other.pk])
        results.append(other)

    # results is non-empty here, so any() decides between real signal and
    # real zeros, never vacuous truth. Rung by rung down the ladder.
    if any(s.score > 0.0 for s in results):
        mode = "weighted"
        results.sort(key=lambda s: (-s.score, -s.popularity))
    elif any(s.estimate > 0.0 for s in results):
        mode = "estimated"
        results.sort(key=lambda s: (-s.estimate, -s.vote_average, -s.vote_count))
    else:
        mode = "rating"
        results.sort(key=lambda s: (-s.vote_average, -s.vote_count))
    return RankedShows(results[:limit], mode=mode)


def stored_similar(show):
    """Read `show`'s Layer 1 ranking from the materialized SimilarShow store.

    The read-path twin of similar_by_people: it returns the same RankedShows
    shape (Show objects carrying `score` and `shared_people`, plus a `mode`),
    but by reading precomputed edges in rank order instead of scoring the graph
    live. The store is rebuilt wholesale after every ingest, so for an unchanged
    catalog these two functions agree row for row. See
    docs/adr/07-materialized-recommendations.md.

    mode comes off the stored edges (every edge of one source carries the
    source's rung, so the first one decides). An empty store for this source
    means no similar shows, returned as an empty RankedShows defaulting to
    "weighted", matching similar_by_people's empty return.

    select_related / prefetch_related mirror what the detail and similar pages
    render off each candidate: the target Show itself, plus its genres and
    networks.
    """
    edges = (
        SimilarShow.objects.filter(source=show)
        .order_by("rank")
        .select_related("target")
        .prefetch_related("target__genres", "target__networks")
    )
    shows = []
    mode = "weighted"
    for edge in edges:
        target = edge.target
        target.score = edge.score
        target.shared_people = edge.shared_people
        mode = edge.mode
        shows.append(target)
    return RankedShows(shows, mode=mode)


def role_index(show):
    """Map every person on `show` to how we score and how we name them.

    The one-show form of role_indexes; see it for what the mapping means.
    """
    return role_indexes([show])[show.id]


def role_indexes(shows):
    """role_index for several shows at once, in a fixed number of queries.

    The scoring half mirrors similar_by_people exactly: best episode_count per
    person, cast and crew merged, service jobs excluded on the crew side. The
    naming half resolves one display role per person: a marquee crew job if
    they hold one (so a creator reads as a creator), otherwise their cast
    character, otherwise a plain crew job. cast_order keeps their best billing
    so the recognizable actors sort to the front of a callout.

    Returns {show_id: {person_id: RoleInfo}}. Used by shared_connections to
    describe why two shows are connected, in the same currency that ranked
    them, and by Layer 2 to read the cast/crew composition of a user's own
    edges (issue #7).

    Bulk because both callers ask about a whole set of shows at once: a detail
    page indexes every candidate it renders, and the connection-type profile
    indexes every show the user rated. Two queries for the set beats two per
    show. Batched under the SQLite variable ceiling (ADR-06).
    """
    show_ids = [s.id for s in shows]
    known = {s.id for s in shows}

    best_count = {}  # (show_id, person_id) -> best episode_count
    name = {}
    cast_order = {}
    character = {}  # -> (episode_count, character) of biggest role
    best_marquee = {}  # -> (rank, job)
    plain_crew = {}  # -> a non-marquee crew job, as a fallback
    marquee_rank = {job: i for i, job in enumerate(MARQUEE_JOBS)}

    for batch in batched(show_ids, SQLITE_MAX_VARS_SAFE):
        for sid, pid, pname, char, order, count in CastMember.objects.filter(
            show_id__in=batch
        ).values_list(
            "show_id",
            "person_id",
            "person__name",
            "character",
            "order",
            "episode_count",
        ):
            key = (sid, pid)
            c = count or 0
            if key not in best_count or c > best_count[key]:
                best_count[key] = c
            name[key] = pname
            if key not in cast_order or order < cast_order[key]:
                cast_order[key] = order
            # Their biggest cast role names them; a lead's main character, not
            # a one-episode second credit.
            if key not in character or c > character[key][0]:
                character[key] = (c, char)

        for sid, pid, pname, job, count in (
            CrewMember.objects.filter(show_id__in=batch)
            .exclude(job__in=SERVICE_JOBS)
            .values_list("show_id", "person_id", "person__name", "job", "episode_count")
        ):
            key = (sid, pid)
            c = count or 0
            if key not in best_count or c > best_count[key]:
                best_count[key] = c
            name[key] = pname
            rank = marquee_rank.get(job)
            if rank is not None:
                if key not in best_marquee or rank < best_marquee[key][0]:
                    best_marquee[key] = (rank, job)
            else:
                plain_crew.setdefault(key, job)

    indexes = {sid: {} for sid in known}
    for key in best_count:
        sid, pid = key
        order = cast_order.get(key, 9999)
        # A recognizable actor is named by their character even when they also
        # crewed (leads often direct or produce an episode); their fame is the
        # character. Everyone else takes their marquee crew role if they hold
        # one, then a plain cast or crew credit.
        if key in character and order < RECOGNIZABLE_BILLING:
            role, kind, mrank = (character[key][1] or "Cast"), "cast", 9999
        elif key in best_marquee:
            rank, job = best_marquee[key]
            role, kind, mrank = job, "marquee", rank
        elif key in character:
            role, kind, mrank = (character[key][1] or "Cast"), "cast", 9999
        else:
            role, kind, mrank = plain_crew.get(key, "Crew"), "crew", 9999
        indexes[sid][pid] = RoleInfo(
            name=name.get(key, ""),
            best_count=best_count[key],
            cast_order=cast_order.get(key, 9999),
            role=role,
            kind=kind,
            marquee_rank=mrank,
        )
    return indexes


def shared_connections(source, source_index, candidate, candidate_index):
    """The people who tie `candidate` back to `source`, richest edge first.

    contribution is the same exponent-weighted min(source share, candidate
    share) that built the score, so the order here is the order that earned
    the ranking. The exponent is monotonic, so applying it cannot reorder this
    list and no ordering test can catch its absence; it is applied anyway so
    that a contribution stays a real share of the score. Anything that later
    divides one by the total would otherwise be quietly wrong. Role and
    kind come from the source side: this is the source show's page, so a person
    is named by what they did on the show you are looking at. Every shared
    person is on the source by construction, so a source-side role always
    exists.

    Returns a list of Connection, longest/strongest edge first. len() equals
    the show's shared_people count, since both dedupe by person the same way.
    """
    src_eps = source.number_of_episodes or 0
    cand_eps = candidate.number_of_episodes or 0

    connections = []
    for pid, info in source_index.items():
        other = candidate_index.get(pid)
        if other is None:
            continue
        src_share = min(info.best_count / src_eps, 1.0) if src_eps else 0.0
        cand_share = min(other.best_count / cand_eps, 1.0) if cand_eps else 0.0
        connections.append(
            Connection(
                name=info.name,
                role=info.role,
                kind=info.kind,
                contribution=min(src_share, cand_share) ** INVOLVEMENT_EXPONENT,
                cast_order=info.cast_order,
                marquee_rank=info.marquee_rank,
                src_count=info.best_count,
                src_episodes=src_eps,
                cand_count=other.best_count,
                cand_episodes=cand_eps,
            )
        )
    # Strongest edge first; billing then name only to keep equal edges stable.
    connections.sort(key=lambda c: (-c.contribution, c.cast_order, c.name))
    return connections


# The two connection types a callout can lean toward, and the tilt a reader's
# learned preference is allowed to apply to them. See
# docs/adr/15-connection-type-preference.md.
#
# Layer 1 scores every shared person the same way whatever they did (ADR-04),
# but a reader does not read them the same way: "they share three of the same
# actors" and "the same showrunner made both" are different pitches. This is the
# only place the distinction exists, and it exists for naming, never for
# scoring. marquee and crew collapse together because the split a reader feels
# is on-screen versus behind it, not marquee versus ordinary.
def connection_type(kind):
    """Which of the two nameable types a RoleInfo/Connection kind belongs to."""
    return "cast" if kind == "cast" else "crew"


# How hard a learned connection-type preference is allowed to bend the named
# order. lean is measured in stars (see PreferenceProfile.connection_type_lean),
# clamped to one star, so a fully cast-leaning reader multiplies cast
# contributions by 1.5 and crew by 0.5: a 3:1 tilt, enough to reorder the
# middle of a callout and push a marginal credit out of the named few, not
# enough to unseat a dominant shared lead. Same instinct as Layer 2's
# re-ranking, where a blowout edge resists personalization (ADR-08).
CONNECTION_TYPE_TILT = 0.5


def connection_type_multipliers(lean):
    """Per-type score multipliers for a signed, cast-positive lean."""
    lean = max(-1.0, min(1.0, lean))
    return {
        "cast": 1.0 + CONNECTION_TYPE_TILT * lean,
        "crew": 1.0 - CONNECTION_TYPE_TILT * lean,
    }


def name_connections(connections, max_named=5, profile=None):
    """Choose the few people to name, and count the rest.

    The rule (issue #2, refined 2026-08-14 from "name by prominence" to "name by
    score"): name the highest-scoring shared people and order them by that
    score, cast and crew as one merged pool. The score is the same episode-share
    contribution that ranked the show, so the single name on a thin row is the
    strongest tie rather than whoever prominence happened to surface, and a
    marquee crew member who scored every episode of both is named ahead of a
    lead who only guested. No reserved cast slot, no role-based ordering, no
    creator guarantee: one pool, sorted by score.

    With a `profile` whose ratings have earned a connection-type lean (issue #7),
    that score is multiplied by the reader's learned preference for cast versus
    crew before the pool is cut and ordered, so a reader whose own highly-rated
    shows are tied together by shared actors sees the actors named first and a
    marginal crew credit fall off the end. The lean is 0.0 for a cold-start
    reader, for one whose ratings have not earned it, and for an anonymous
    visitor, and a 0.0 lean makes both multipliers 1.0: the paragraph below is
    then exactly the pre-#7 behaviour, not an approximation of it.

    Only recognizable cast (billed above RECOGNIZABLE_BILLING) and marquee crew
    are eligible to be named. The long tail of bit players and technical crew
    still collapses into a number; that is a separate decision (issue #2: "collapse
    the long tail of bit players and technical crew into a number") that
    name-by-score does not touch. Merging is within this eligible pool: it drops
    the old cast-first reservation, not the collapse of the tail. When a
    candidate shares neither recognizable cast nor marquee crew, the strongest
    edges are named so the callout is never a bare count.

    Returns (named, others): the Connections to name, highest score first, and
    the count left over. others is len(connections) - len(named).
    """
    # connections arrive from shared_connections already sorted by
    # (-contribution, cast_order, name), so this filter preserves highest-score
    # first without re-sorting; cast and crew compete in the one order.
    eligible = [
        c
        for c in connections
        if c.kind == "marquee" or (c.kind == "cast" and c.cast_order < RECOGNIZABLE_BILLING)
    ]

    lean = getattr(profile, "connection_type_lean", 0.0) or 0.0
    if lean and eligible:
        multiplier = connection_type_multipliers(lean)
        # Same sort keys as shared_connections, so with a 0.0 lean this is a
        # no-op re-sort of an already-sorted list and equal edges still break on
        # billing then name.
        eligible.sort(
            key=lambda c: (
                -c.contribution * multiplier[connection_type(c.kind)],
                c.cast_order,
                c.name,
            )
        )
    named = eligible[:max_named]

    # Neither recognizable cast nor marquee crew: name the strongest edges so
    # the callout is never just a bare count. Untilted on purpose: this is the
    # "never a bare count" floor, and there is no type preference to express
    # when the candidate shares no nameable person of either type.
    if not named:
        named = list(connections[:3])

    return named, len(connections) - len(named)


# How a crew role reads in prose: a noun to introduce the person, a verb phrase
# framed as "both" (every named person is, by construction, on both shows), and
# a short "so did these people" phrase for everyone else who held the same role.
# DIRECTED and SHOT are placeholders resolved against the source-side episode
# count so a one-off guest director reads as "directed one episode", not
# "directed both". Roles absent here fall back to a lowercased job and "worked on
# both"; the graph rarely names a plain crew job, but the callout never breaks.
#
# The third phrase exists because a callout that names four directors used to
# repeat the noun four times: "director X directed one episode and director Y
# directed one episode" (issue #4). Only the strongest holder of a role now gets
# the full clause and the rest collapse behind it, which is exactly what the cast
# side has always done with "and Leslie Hope and Carlos Bernard appear too". It
# is past tense throughout, so one phrase serves any number of people.
ROLE_PROSE = {
    "Creator": ("creator", "created both", "created it too"),
    "Showrunner": ("showrunner", "ran both shows", "ran it too"),
    "Executive Producer": ("executive producer", "produced both", "produced too"),
    "Producer": ("producer", "produced both", "produced too"),
    "Co-Executive Producer": ("producer", "produced both", "produced too"),
    "Co-Producer": ("producer", "produced both", "produced too"),
    "Writer": ("writer", "wrote for both", "wrote too"),
    "Original Music Composer": ("composer", "scored both", "scored too"),
    "Composer": ("composer", "scored both", "scored too"),
    "Music": ("composer", "scored both", "scored too"),
    "Music Supervisor": (
        "music supervisor",
        "supervised the music on both",
        "supervised too",
    ),
    "Director": ("director", "DIRECTED", "directed too"),
    "Director of Photography": ("cinematographer", "SHOT", "shot too"),
    "Cinematographer": ("cinematographer", "SHOT", "shot too"),
    "Editor": ("editor", "edited both", "edited too"),
}

# What an unlisted crew job falls back to, so ROLE_PROSE.get always unpacks
# into three.
DEFAULT_ROLE_PROSE = ("crew", "worked on both", "worked on it too")

# Verb phrases that read naturally sharpened to "every episode of both" when the
# person is on the whole run of each show. "created both" and "ran both shows"
# do not take the upgrade (a creator is not measured in episodes), so they are
# left off this set.
UPGRADABLE_TO_EVERY = {"produced both", "scored both", "edited both"}

_NUMBER_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
]


def _num_word(n):
    """Spell small counts, digits for the rest. 'directed one episode' reads
    better than 'directed 1 episode'; 'across 28 episodes' better than
    'across twenty-eight'."""
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def _episodes(n):
    return f"{_num_word(n)} episode{'' if n == 1 else 's'}"


def _text(v):
    return {"t": "text", "v": v}


def _name(v):
    return {"t": "name", "v": v}


def _join_names(names):
    """['A'] -> [A]; ['A','B'] -> [A, ' and ', B]; ['A','B','C'] -> A, B and C.
    Returns a segment list so each name stays its own amber-styled token."""
    segs = []
    for i, nm in enumerate(names):
        if i:
            segs.append(_text(" and " if i == len(names) - 1 else ", "))
        segs.append(_name(nm))
    return segs


def _cast_clause(c):
    """The strongest shared actor, named by their character on the source show
    with that show's episode count: 'Giancarlo Esposito plays Gus Fring across
    28 episodes', 'Aaron Paul plays Jesse Pinkman in all 62 episodes'.

    Counts are source-side on purpose. This is the source show's page, so the
    character and the count both describe who the person is here, and their
    presence in the list is what asserts the tie to the recommended show. Mixing
    a source character with a candidate count would misread (a lead of the
    candidate who only guested on the source), so both stay on the same side.
    """
    char = c.role if c.role and c.role != "Cast" else None
    if not char:
        return [_name(c.name), _text(" appears")]
    if c.src_count and c.src_episodes and c.src_count >= c.src_episodes:
        tail = f" plays {char} in all {c.src_episodes} episodes"
    elif c.src_count:
        tail = f" plays {char} across {_episodes(c.src_count)}"
    else:
        tail = f" plays {char}"
    return [_name(c.name), _text(tail)]


def _secondary_cast_clause(cast):
    verb = "appears too" if len(cast) == 1 else "appear too"
    return _join_names([c.name for c in cast]) + [_text(f" {verb}")]


def _role_prose(c):
    """The (noun, verb, also) prose for one connection's crew role."""
    default = ((c.role or "crew").lower(),) + DEFAULT_ROLE_PROSE[1:]
    return ROLE_PROSE.get(c.role, default)


def _crew_clause(c):
    """A crew tie as prose: 'composer Dave Porter scored every episode of both',
    'director Tim Hunter directed one episode'."""
    noun, verb, _also = _role_prose(c)
    if verb == "DIRECTED":
        phrase = f"directed {_episodes(c.src_count)}" if c.src_count else "directed both"
    elif verb == "SHOT":
        phrase = f"shot {_episodes(c.src_count)}" if c.src_count else "shot both"
    else:
        phrase = verb
        src_every = c.src_episodes and c.src_count >= c.src_episodes
        cand_every = c.cand_episodes and c.cand_count >= c.cand_episodes
        if verb in UPGRADABLE_TO_EVERY and src_every and cand_every:
            phrase = verb.replace("both", "every episode of both")
    return [_text(noun + " "), _name(c.name), _text(" " + phrase)]


def _secondary_crew_clause(crew):
    """The other holders of a role, behind the one who earned the full clause.

    The crew mirror of _secondary_cast_clause. 'Kevin Hooks, Dwight H. Little
    and Milan Cheylov directed too' says the same thing as three more "director
    X directed N episodes" clauses without saying "director" three more times
    (issue #4). The episode counts of the collapsed members are the cost, and
    they are the least interesting numbers in the sentence: the strongest holder
    of the role keeps theirs, and the tail was already being read as a list of
    names rather than a table of counts.
    """
    _noun, _verb, also = _role_prose(crew[0])
    return _join_names([c.name for c in crew]) + [_text(f" {also}")]


def _crew_clauses(crew):
    """Every named crew member, grouped so a role noun is said once.

    Groups by prose noun rather than raw job, so "Executive Producer" and
    "Co-Executive Producer" collapse together the way they already read
    together. First appearance sets a group's position, and within a group the
    order name_connections gave is preserved, so the strongest tie of each role
    is the one that keeps its full clause.
    """
    groups = {}
    for c in crew:
        groups.setdefault(_role_prose(c)[0], []).append(c)

    clauses = []
    for members in groups.values():
        clauses.append(_crew_clause(members[0]))
        if len(members) > 1:
            clauses.append(_secondary_crew_clause(members[1:]))
    return clauses


def compose_callout(source, candidate, connections, named, others, profile=None):
    """Turn a candidate's shared people into one flowing prose sentence.

    The 7a treatment (issue #2): lead with the recognizable actor named by their
    character and episode count, gather any other named actors, then name the
    marquee crew by what they did on both shows, and collapse the long tail into
    a number. No editorial header: the sentence opens straight on the connection
    (decided 2026-08-14; the earlier "A thinner thread:" / "Made by the same
    people:" leads are gone). Composition lives here in Python, not the template,
    so it stays testable.

    Which of the two blocks opens the sentence is the reader's, not the
    catalog's (issue #7, amending issue #2's fixed "pitch by cast" order on
    2026-08-26). A reader whose own ratings say their shows hang together on
    crew hears the crew first. Every other reader, which is cold start,
    insufficient signal, anonymous, and anyone whose two affinities landed
    within half a star of each other, has a lean of exactly 0.0 and gets cast
    first: the same default, reached by the same path, not a repaired version
    of it. See docs/adr/15-connection-type-preference.md.

    Returns a dict for the template:
        segments  an ordered list of {"t": "text"|"name", "v": str}, so the
                  shared people render as amber tokens and everything else as
                  quiet prose, all auto-escaped
        shared_total  the candidate's shared-people count

    The names come pre-ordered by name_connections (highest score first), so
    whichever block opens, it opens on the strongest tie of its kind.
    """
    cast = [c for c in named if c.kind == "cast"]
    crew = [c for c in named if c.kind in ("marquee", "crew")]

    cast_clauses = []
    if cast:
        cast_clauses.append(_cast_clause(cast[0]))
        if len(cast) > 1:
            cast_clauses.append(_secondary_cast_clause(cast[1:]))

    lean = getattr(profile, "connection_type_lean", 0.0) or 0.0
    blocks = (
        [_crew_clauses(crew), cast_clauses] if lean < 0 else [cast_clauses, _crew_clauses(crew)]
    )
    clauses = [clause for block in blocks for clause in block]
    if not clauses:  # neither cast nor crew named: fall back to bare names
        clauses.append(_join_names([c.name for c in named]))

    segments = []
    for i, clause in enumerate(clauses):
        if i:
            segments.append(_text(" and " if i == len(clauses) - 1 else ", "))
        segments.extend(clause)

    # With no lead phrase, open on a capital. A cast clause starts on a name and
    # needs no help; a crew-only clause starts on a lowercased role noun.
    if segments and segments[0]["t"] == "text":
        segments[0] = _text(segments[0]["v"][:1].upper() + segments[0]["v"][1:])

    if others > 0:
        segments.append(_text(f", with {others} other{'' if others == 1 else 's'}."))
    else:
        segments.append(_text("."))

    return {"segments": segments, "shared_total": len(connections)}


def similar_by_cast(show, limit=12):
    """Return shows ranked by how many cast members they share with `show`.

    Each result carries a `shared_cast` attribute, the number of distinct
    people who appear in both shows. Ties break on popularity so the ordering
    stays stable between calls.

    Returns an empty queryset when the show has no cast recorded.
    """
    # A subquery, not a materialised id list: the person set is only needed to
    # join, never in Python, so pushing it into SQL removes both the extra query
    # and SQLite's per-person variable ceiling. An empty subquery yields no
    # rows, so the no-cast case still returns an empty queryset.
    # See docs/adr/06-sql-variable-ceiling.md.
    return (
        Show.objects.filter(
            cast__person_id__in=CastMember.objects.filter(show=show).values("person_id")
        )
        .exclude(pk=show.pk)
        .annotate(shared_cast=Count("cast__person_id", distinct=True))
        .order_by("-shared_cast", "-popularity")
        .prefetch_related("genres")[:limit]
    )


def similar_by_crew(show, limit=12):
    """Return shows ranked by how many crew members they share with `show`.

    Each result carries a `shared_crew` attribute. Service jobs are excluded
    from both sides of the comparison, so the count reflects people who shaped
    the show rather than people the studio hired for both productions.

    Returns an empty queryset when the show has no qualifying crew recorded.
    """
    # A subquery, not a materialised id list: the person set only feeds the
    # join, so pushing it into SQL removes both the extra query and SQLite's
    # per-person variable ceiling. The SERVICE_JOBS exclusion stays inside the
    # subquery so casting rows never seed the source set. An empty subquery
    # yields no rows, so the no-crew case still returns an empty queryset.
    # See docs/adr/06-sql-variable-ceiling.md.
    source_person_ids = (
        CrewMember.objects.filter(show=show).exclude(job__in=SERVICE_JOBS).values("person_id")
    )

    # Both conditions sit in one filter() call so they apply to the same join.
    # Splitting them into .filter().exclude() would drop every show that has
    # any casting crew at all, rather than ignoring the casting rows.
    return (
        Show.objects.filter(
            Q(crew__person_id__in=source_person_ids) & ~Q(crew__job__in=SERVICE_JOBS)
        )
        .exclude(pk=show.pk)
        .annotate(shared_crew=Count("crew__person_id", distinct=True))
        .order_by("-shared_crew", "-popularity")
        .prefetch_related("genres")[:limit]
    )
