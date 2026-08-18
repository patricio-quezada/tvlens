"""Shows app views."""

from django.contrib import messages
from django.contrib.auth import login
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from .forms import RegistrationForm
from .models import Genre, Show
from .recommenders import (
    compose_callout,
    name_connections,
    role_index,
    shared_connections,
    similar_by_cast,
    similar_by_crew,
    stored_similar,
)


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
        # TODO(Q-10 follow-up): compute favorite_genre_ids from user's ≥4-star ratings

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

    Layer 1 becomes visible here. The ranking is the materialized graph's
    (stored_similar reads the same order similar_by_people would compute live,
    ADR-07); this view only describes each edge: for every recommendation it
    composes one prose sentence naming the people who tie it back, cast and crew,
    ordered by their episode-share contribution, and hands the template the
    honest caption from the mode.
    """
    show = get_object_or_404(
        Show.objects.prefetch_related("genres", "networks"), slug=slug
    )
    ranked = stored_similar(show)

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
        },
    )


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
