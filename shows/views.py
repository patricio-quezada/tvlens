"""Shows app views."""

from django.contrib import messages
from django.contrib.auth import login
from django.db.models import Count
from django.shortcuts import redirect, render

from .forms import RegistrationForm
from .models import Genre, Show


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
