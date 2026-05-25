"""Shows app views."""

from django.contrib import messages
from django.contrib.auth import login
from django.db.models import Count
from django.shortcuts import redirect, render

from .forms import RegistrationForm
from .models import Genre, Show


def index(request):
    base_qs = Show.objects.prefetch_related("genres")
    popular = base_qs.all()[:12]
    top_rated = base_qs.filter(vote_count__gte=50).order_by("-vote_average")[:12]
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
            "popular": popular,
            "top_rated": top_rated,
            "recently_added": recently_added,
            "genres": genres,
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
