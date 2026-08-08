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


class RankedShows(list):
    """similar_by_people's return value: shows plus one fact about the order.

    rating_fallback is True when the graph had no signal, every candidate
    scored 0.0, and the list fell back to TMDb rating order. The template
    reads it to caption the list honestly. A plain list plus one attribute,
    so callers that expect a list keep working.
    """

    def __init__(self, shows=(), rating_fallback=False):
        super().__init__(shows)
        self.rating_fallback = rating_fallback


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
    shared edge is a null-count series-level credit. Decided on QUE-11
    (2026-08-07): the order, and only the order, falls back to TMDb rating,
    vote_average descending with vote_count breaking ties so a 10.0 on three
    votes cannot beat an 8.9 on ten thousand. Rating is a quality signal;
    popularity is the engagement metric this recommender exists to avoid.
    The candidate set does not change, still only shows sharing at least one
    qualifying person.

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
        for person_id, other_count in best_by_show[other.pk].items():
            own_ratio = min(own_best[person_id] / own_episodes, 1.0) if own_episodes else 0.0
            other_ratio = min(other_count / other_episodes, 1.0) if other_episodes else 0.0
            score += min(own_ratio, other_ratio)
        other.score = score
        other.shared_people = len(best_by_show[other.pk])
        results.append(other)

    # results is non-empty here, so all() means real zeros, not vacuous truth.
    rating_fallback = all(s.score == 0.0 for s in results)
    if rating_fallback:
        results.sort(key=lambda s: (-s.vote_average, -s.vote_count))
    else:
        results.sort(key=lambda s: (-s.score, -s.popularity))
    return RankedShows(results[:limit], rating_fallback=rating_fallback)


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
