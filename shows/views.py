"""Shows app views."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import RegistrationForm
from .models import Genre, Rating, Show
from .personalization import rerank
from .recommenders import (
    compose_callout,
    name_connections,
    role_index,
    shared_connections,
    similar_by_cast,
    similar_by_crew,
    stored_similar,
)

# The MovieLens half-star scale, 0.5 to 5.0 (the same scale ADR-08 assumes for
# Layer 2, enforced by Rating's model validators). Every value the widget offers
# and the only values rate() will store. Multiples of 0.5 are exact in binary,
# so equality checks against this set never suffer float drift.
VALID_SCORES = [n / 2 for n in range(1, 11)]


def star_steps(user_rating):
    """The ten half-star inputs for the rating widget, high to low.

    The template renders these in DOM order 5.0 -> 0.5 so the pure-CSS widget can
    fill "this star and every lower one" with a sibling selector (see detail.html).
    Each even half-step is a full star, each odd one the left half of the next.
    """
    steps = []
    for n in range(10, 0, -1):
        value = n / 2
        steps.append({
            "value": value,
            "css_class": "full" if n % 2 == 0 else "half",
            "checked": user_rating == value,
        })
    return steps


def index(request):
    base_qs = Show.objects.prefetch_related("genres")

    top_picks: list = []
    side_quests: list = []
    favorite_genre_ids: set = set()
    top_picks_title = None

    if request.user.is_authenticated:
        top_picks_title = f"Top Picks for {request.user.username}"
        # TODO(Q-17/Q-18): populate top_picks from personalized recommender
        # TODO(Q-20): populate side_quests from cross-genre neighborhood walk
        # A favorite genre is one the user has rated >= 4 stars (the same "high"
        # line Layer 2 personalizes from, ADR-08). The template glows these genre
        # pills and cards so the page shows what it thinks the user likes.
        favorite_genre_ids = set(
            Genre.objects.filter(
                shows__ratings__user=request.user,
                shows__ratings__score__gte=4.0,
            )
            .values_list("id", flat=True)
            .distinct()
        )

    recently_added = base_qs.order_by("-created_at")[:12]
    genres = (
        Genre.objects.annotate(n=Count("shows"))
        .filter(n__gt=0)
        .order_by("-n")
    )
    return render(
        request,
        "shows/index.html",
        {
            "top_picks": top_picks,
            "top_picks_title": top_picks_title,
            "side_quests": side_quests,
            "recently_added": recently_added,
            "genres": genres,
            "favorite_genre_ids": favorite_genre_ids,
        },
    )


def detail(request, slug):
    """One show, then the shows it connects to through shared people.

    Layer 1 supplies the candidate list (stored_similar reads the same order
    similar_by_people would compute live, ADR-07); Layer 2 then re-ranks that
    list for the signed-in user by their learned genre/tag preferences (ADR-08).
    For every recommendation the view composes one prose sentence naming the
    people who tie it back, cast and crew, ordered by their episode-share
    contribution, and hands the template the honest caption from the mode.
    """
    show = get_object_or_404(
        Show.objects.prefetch_related("genres", "networks"), slug=slug
    )
    # Layer 1 order in, personalized order out. Anonymous and ratingless users
    # get the cold-start ordering (Layer 1 under a light quality prior).
    ranked = rerank(request.user, stored_similar(show))

    # TVLens's own rating (distinct from the TMDb vote_average in the hero). The
    # widget shows the signed-in user their current score and lets them change it.
    user_rating = None
    if request.user.is_authenticated:
        user_rating = (
            Rating.objects.filter(user=request.user, show=show)
            .values_list("score", flat=True)
            .first()
        )

    source_index = role_index(show)
    recommendations = []
    for candidate in ranked:
        connections = shared_connections(
            show, source_index, candidate, role_index(candidate)
        )
        named, others = name_connections(connections)
        callout = compose_callout(show, candidate, connections, named, others)
        recommendations.append({"show": candidate, "callout": callout})

    return render(
        request,
        "shows/detail.html",
        {
            "show": show,
            "recommendations": recommendations,
            "mode": ranked.mode,
            "personalized": ranked.personalized,
            "user_rating": user_rating,
            "star_steps": star_steps(user_rating),
            "average_rating": show.average_rating,
            "rating_count": show.ratings.count(),
        },
    )


@login_required
@require_POST
def rate(request, slug):
    """Record or update the signed-in user's half-star rating for one show.

    This is how TVLens collects its OWN ratings (the cold-start data Layer 2
    needs, #6), so the stored shape is exactly one per-user score in 0.5 steps.
    One row per (user, show): update_or_create moves the existing row on a
    re-rate instead of piling up duplicates, which Rating's unique_together
    would reject anyway. The widget only ever POSTs a valid half-step, but this
    is a public endpoint, so the 0.5-to-5.0 bounds and the half-step are
    enforced here too rather than trusted from the client.
    """
    show = get_object_or_404(Show, slug=slug)
    try:
        score = float(request.POST.get("score", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Rating must be a number.")
    if score not in VALID_SCORES:
        return HttpResponseBadRequest(
            "Rating must be a half-star step between 0.5 and 5.0."
        )
    Rating.objects.update_or_create(
        user=request.user, show=show, defaults={"score": score}
    )
    messages.success(request, f"You rated {show.name} {score:g} stars.")
    return redirect(show.get_absolute_url())


def similar(request, pk):
    """Shows connected to this one by shared people. Layer 1, all edges."""
    show = get_object_or_404(Show, pk=pk)
    return render(
        request,
        "shows/similar.html",
        {
            "show": show,
            "by_people": stored_similar(show),
            "by_cast": similar_by_cast(show),
            "by_crew": similar_by_crew(show),
        },
    )


def register(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome to TVLens, {user.username}!")
            return redirect("shows:index")
    else:
        form = RegistrationForm()
    return render(request, "registration/register.html", {"form": form})
