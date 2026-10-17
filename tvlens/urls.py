"""TVLens URL configuration."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from shows.views import register


def healthz(request):
    """Fly's health check. Answers without the database, so a slow query or a
    locked SQLite file never gets a healthy machine restarted."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("accounts/register/", register, name="register"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("shows.urls")),
]
