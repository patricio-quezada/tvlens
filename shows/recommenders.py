"""Layer 1 of the TVLens recommender: the static content graph.

Shows connect to each other through what they share. The first and strongest
edge is cast. Two shows that share several actors are related in a way that
popularity rankings and genre labels cannot see.

Later layers stack crew, genre, and learned weights on top of this edge.
See QUE-5 for the full design.
"""

from django.db.models import Count

from .models import CastMember, Show


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
