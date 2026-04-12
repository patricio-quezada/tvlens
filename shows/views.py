"""Shows app views."""

from django.shortcuts import render

from .models import Show


def index(request):
    shows = Show.objects.all()[:24]
    return render(request, "shows/index.html", {"shows": shows})
