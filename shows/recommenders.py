"""Layer 1 of the TVLens recommender: the static content graph.

Shows connect to each other through what they share. The first edge is cast.
Two shows that share several actors are related in a way that popularity
rankings and genre labels cannot see.

The second edge is crew, which is denser than cast and catches franchise
structure that cast cannot. It needs one correction: not every job on a
production says something about the show. See SERVICE_JOBS below.

The merged edge weights every shared person by episode share, so a lead who
carried a whole run outranks a crowd of one-episode guests. See
similar_by_people and QUE-9 for the four decisions behind it.

Later layers stack genre and learned weights on top of these edges.
See QUE-5 for the full design.
"""

from collections import namedtuple

from django.db.models import Count, Q

from .models import CastMember, CrewMember, Show

# Jobs that connect two productions without connecting the two shows.
#
# A casting office works across an entire studio slate, so counting it makes
# unrelated shows look related. On the 100-show catalog it linked The Boys to
# Grey's Anatomy on four shared people, every one of them a casting director.
# Excluding these costs two shows of coverage and removes thirteen false pairs.
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

# Below this, even the strongest shared person is a light tie: a guest across a
# handful of episodes, not a lead who carried a run. The callout says so out
# loud ("A thinner thread:") rather than dressing a weak edge up as a strong
# one. 0.15 is one seventh of a run; calibrated on the catalog, it separates
# Breaking Bad's real ties (Better Call Saul 1.0, The Boys 0.30, Westworld 0.44,
# The Mandalorian 0.38) from the thin ones (Friends 0.10, Suits 0.11, CSI 0.02).
# Read off contribution, the same episode-share the score is built from.
THIN_THRESHOLD = 0.15

# What we know about one person on one show: enough to score them (best_count,
# mirroring similar_by_people) and to name them (role/kind/cast_order). role is
# resolved once per person so a creator reads as a creator and an actor by their
# character. kind is "marquee" (a MARQUEE_JOBS crew role), "cast", or "crew".
RoleInfo = namedtuple(
    "RoleInfo", "name best_count cast_order role kind marquee_rank"
)

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

    The rule, decided on QUE-9:

        score(A, B) = sum over shared people of
            min(episode_count on A / A.number_of_episodes,
                episode_count on B / B.number_of_episodes)

    Relative counts make cast and crew the same unit, episode share, so the
    two edges merge without a cast-versus-crew exchange rate. `min` means the
    edge takes the weaker end: a Breaking Bad lead who did three episodes of
    Better Call Saul does not create a strong edge. No floor: every shared
    person contributes their share, however small. Service jobs stay excluded
    on both sides because a casting office links productions, not shows, and
    sixty episodes of casting credit does not change that.

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
    shared edge is a null-count series-level credit. Decided on QUE-11 and
    revised the same day (2026-08-07), the order falls down a ladder, and
    only the order, the candidate set never changes:

        1. weighted   sum of min(source share, candidate share)
        2. estimated  the source side is unknowable, so rank by the half we
                      can see: the sum of candidate-side shares. A candidate
                      whose shared person carried their whole show outranks
                      one where they were a one-episode guest.
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
    person_ids = list(own_best)
    best_by_show = {}
    for show_id, person_id, count in (
        CastMember.objects.filter(person_id__in=person_ids)
        .exclude(show=show)
        .values_list("show_id", "person_id", "episode_count")
    ):
        fold_best(best_by_show.setdefault(show_id, {}), person_id, count)
    for show_id, person_id, count in (
        CrewMember.objects.filter(person_id__in=person_ids)
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
            score += min(own_ratio, other_ratio)
            estimate += other_ratio
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


def role_index(show):
    """Map every person on `show` to how we score and how we name them.

    The scoring half mirrors similar_by_people exactly: best episode_count per
    person, cast and crew merged, service jobs excluded on the crew side. The
    naming half resolves one display role per person: a marquee crew job if
    they hold one (so a creator reads as a creator), otherwise their cast
    character, otherwise a plain crew job. cast_order keeps their best billing
    so the recognizable actors sort to the front of a callout.

    Returns {person_id: RoleInfo}. Used by shared_connections to describe why
    two shows are connected, in the same currency that ranked them.
    """
    marquee_rank = {job: i for i, job in enumerate(MARQUEE_JOBS)}

    best_count = {}
    name = {}
    cast_order = {}
    character = {}      # person_id -> (episode_count, character) of biggest role
    best_marquee = {}   # person_id -> (rank, job)
    plain_crew = {}     # person_id -> a non-marquee crew job, as a fallback

    for pid, pname, char, order, count in CastMember.objects.filter(
        show=show
    ).values_list(
        "person_id", "person__name", "character", "order", "episode_count"
    ):
        c = count or 0
        if pid not in best_count or c > best_count[pid]:
            best_count[pid] = c
        name[pid] = pname
        if pid not in cast_order or order < cast_order[pid]:
            cast_order[pid] = order
        # Their biggest cast role names them; a lead's main character, not a
        # one-episode second credit.
        if pid not in character or c > character[pid][0]:
            character[pid] = (c, char)

    for pid, pname, job, count in (
        CrewMember.objects.filter(show=show)
        .exclude(job__in=SERVICE_JOBS)
        .values_list("person_id", "person__name", "job", "episode_count")
    ):
        c = count or 0
        if pid not in best_count or c > best_count[pid]:
            best_count[pid] = c
        name[pid] = pname
        rank = marquee_rank.get(job)
        if rank is not None:
            if pid not in best_marquee or rank < best_marquee[pid][0]:
                best_marquee[pid] = (rank, job)
        else:
            plain_crew.setdefault(pid, job)

    index = {}
    for pid in best_count:
        order = cast_order.get(pid, 9999)
        # A recognizable actor is named by their character even when they also
        # crewed (leads often direct or produce an episode); their fame is the
        # character. Everyone else takes their marquee crew role if they hold
        # one, then a plain cast or crew credit.
        if pid in character and order < RECOGNIZABLE_BILLING:
            role, kind, mrank = (character[pid][1] or "Cast"), "cast", 9999
        elif pid in best_marquee:
            rank, job = best_marquee[pid]
            role, kind, mrank = job, "marquee", rank
        elif pid in character:
            role, kind, mrank = (character[pid][1] or "Cast"), "cast", 9999
        else:
            role, kind, mrank = plain_crew.get(pid, "Crew"), "crew", 9999
        index[pid] = RoleInfo(
            name=name.get(pid, ""),
            best_count=best_count[pid],
            cast_order=cast_order.get(pid, 9999),
            role=role,
            kind=kind,
            marquee_rank=mrank,
        )
    return index


def shared_connections(source, source_index, candidate, candidate_index):
    """The people who tie `candidate` back to `source`, richest edge first.

    contribution is the same min(source share, candidate share) that built the
    score, so the order here is the order that earned the ranking. Role and
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
                contribution=min(src_share, cand_share),
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


def name_connections(connections, max_named=5):
    """Choose the few people to name, and count the rest.

    The rule (QUE-2, refined 2026-08-14 from "name by prominence" to "name by
    score"): name the highest-scoring shared people and order them by that
    score, cast and crew as one merged pool. The score is the same episode-share
    contribution that ranked the show, so the single name on a thin row is the
    strongest tie rather than whoever prominence happened to surface, and a
    marquee crew member who scored every episode of both is named ahead of a
    lead who only guested. No reserved cast slot, no role-based ordering, no
    creator guarantee: one pool, sorted by score.

    Only recognizable cast (billed above RECOGNIZABLE_BILLING) and marquee crew
    are eligible to be named. The long tail of bit players and technical crew
    still collapses into a number; that is a separate decision (QUE-2: "collapse
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
        c for c in connections
        if c.kind == "marquee"
        or (c.kind == "cast" and c.cast_order < RECOGNIZABLE_BILLING)
    ]
    named = eligible[:max_named]

    # Neither recognizable cast nor marquee crew: name the strongest edges so
    # the callout is never just a bare count.
    if not named:
        named = list(connections[:3])

    return named, len(connections) - len(named)


# How a crew role reads in prose: a noun to introduce the person and a verb
# phrase framed as "both", since every named person is, by construction, on both
# shows. DIRECTED and SHOT are placeholders resolved against the candidate-side
# episode count so a one-off guest director reads as "directed one episode", not
# "directed both". Roles absent here fall back to a lowercased job and "worked on
# both"; the graph rarely names a plain crew job, but the callout never breaks.
ROLE_PROSE = {
    "Creator": ("creator", "created both"),
    "Showrunner": ("showrunner", "ran both shows"),
    "Executive Producer": ("executive producer", "produced both"),
    "Producer": ("producer", "produced both"),
    "Co-Executive Producer": ("producer", "produced both"),
    "Co-Producer": ("producer", "produced both"),
    "Writer": ("writer", "wrote for both"),
    "Original Music Composer": ("composer", "scored both"),
    "Composer": ("composer", "scored both"),
    "Music": ("composer", "scored both"),
    "Music Supervisor": ("music supervisor", "supervised the music on both"),
    "Director": ("director", "DIRECTED"),
    "Director of Photography": ("cinematographer", "SHOT"),
    "Cinematographer": ("cinematographer", "SHOT"),
    "Editor": ("editor", "edited both"),
}

# Verb phrases that read naturally sharpened to "every episode of both" when the
# person is on the whole run of each show. "created both" and "ran both shows"
# do not take the upgrade (a creator is not measured in episodes), so they are
# left off this set.
UPGRADABLE_TO_EVERY = {"produced both", "scored both", "edited both"}

_NUMBER_WORDS = [
    "zero", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine",
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


def _crew_clause(c):
    """A crew tie as prose: 'composer Dave Porter scored every episode of both',
    'director Tim Hunter directed one episode'."""
    noun, verb = ROLE_PROSE.get(
        c.role, ((c.role or "crew").lower(), "worked on both")
    )
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


def _select_lead(connections, named):
    """Pick one lead phrase from the connection profile, or none. A small,
    data-driven set (QUE-2 wireframe): a shared creator says 'same people'; a
    weak strongest edge says 'thinner thread'; an all-cast tie says the actors
    are the whole story. Most rows earn no lead and open on the strongest name.
    Precedence matters: a genuinely thin all-cast tie is named honestly as thin
    rather than oversold as 'N actors carry over'."""
    total = len(connections)
    if any(c.kind == "marquee" and c.marquee_rank <= 1 for c in connections):
        return "Made by the same people:"
    if connections and connections[0].contribution < THIN_THRESHOLD:
        return "A thinner thread:"
    if connections and all(c.kind == "cast" for c in connections):
        if total == 1:
            return "One actor carries over:"
        return f"{_num_word(total).capitalize()} actors carry over:"
    return ""


def compose_callout(source, candidate, connections, named, others):
    """Turn a candidate's shared people into one flowing prose sentence.

    The 7a treatment (QUE-2): lead with the recognizable actor named by their
    character and episode count, gather any other named actors, then name the
    marquee crew by what they did on both shows, and collapse the long tail into
    a number. A profile-chosen lead phrase sets the framing. Composition lives
    here in Python, not the template, so it stays testable.

    Returns a dict for the template:
        lead      an optional opening phrase, already punctuated
        segments  an ordered list of {"t": "text"|"name", "v": str}, so the
                  shared people render as amber tokens and everything else as
                  quiet prose, all auto-escaped
        shared_total  the candidate's shared-people count

    The names come pre-ordered by name_connections (cast by prominence, then
    marquee crew), so the prose leads with the pitch and the reasoning follows.
    """
    lead = _select_lead(connections, named)
    cast = [c for c in named if c.kind == "cast"]
    crew = [c for c in named if c.kind in ("marquee", "crew")]

    clauses = []
    if cast:
        clauses.append(_cast_clause(cast[0]))
        if len(cast) > 1:
            clauses.append(_secondary_cast_clause(cast[1:]))
    for c in crew:
        clauses.append(_crew_clause(c))
    if not clauses:  # neither cast nor crew named: fall back to bare names
        clauses.append(_join_names([c.name for c in named]))

    segments = []
    for i, clause in enumerate(clauses):
        if i:
            segments.append(_text(" and " if i == len(clauses) - 1 else ", "))
        segments.extend(clause)

    # Open on a capital when no lead phrase already did. A cast clause starts on
    # a name and needs no help; a crew-only clause starts on a lowercased noun.
    if not lead and segments and segments[0]["t"] == "text":
        segments[0] = _text(segments[0]["v"][:1].upper() + segments[0]["v"][1:])

    if others > 0:
        segments.append(_text(f", with {others} other{'' if others == 1 else 's'}."))
    else:
        segments.append(_text("."))

    return {"lead": lead, "segments": segments, "shared_total": len(connections)}


def similar_by_cast(show, limit=12):
    """Return shows ranked by how many cast members they share with `show`.

    Each result carries a `shared_cast` attribute, the number of distinct
    people who appear in both shows. Ties break on popularity so the ordering
    stays stable between calls.

    Returns an empty queryset when the show has no cast recorded.
    """
    person_ids = list(
        CastMember.objects.filter(show=show).values_list("person_id", flat=True)
    )
    if not person_ids:
        return Show.objects.none()

    return (
        Show.objects.filter(cast__person_id__in=person_ids)
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
    person_ids = list(
        CrewMember.objects.filter(show=show)
        .exclude(job__in=SERVICE_JOBS)
        .values_list("person_id", flat=True)
    )
    if not person_ids:
        return Show.objects.none()

    # Both conditions sit in one filter() call so they apply to the same join.
    # Splitting them into .filter().exclude() would drop every show that has
    # any casting crew at all, rather than ignoring the casting rows.
    return (
        Show.objects.filter(
            Q(crew__person_id__in=person_ids) & ~Q(crew__job__in=SERVICE_JOBS)
        )
        .exclude(pk=show.pk)
        .annotate(shared_crew=Count("crew__person_id", distinct=True))
        .order_by("-shared_crew", "-popularity")
        .prefetch_related("genres")[:limit]
    )
