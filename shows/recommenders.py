"""Layer 1 of the TVLens recommender: the static content graph.

Shows connect to each other through what they share. The first edge is cast.
Two shows that share several actors are related in a way that popularity
rankings and genre labels cannot see.

The second edge is crew, which is denser than cast and catches franchise
structure that cast cannot. It needs one correction: not every job on a
production says something about the show. See SERVICE_JOBS below.

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
SERVICE_JOBS = [
    "Casting",
    "Original Casting",
    "Casting Associate",
    "Casting Assistant",
    "Casting Director",
    "Local Casting",
    "Voice Casting",
]


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
