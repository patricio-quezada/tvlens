"""TVLens URL configuration."""

from django.contrib import admin
from django.urls import include, path

from shows.views import register

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/register/", register, name="register"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("shows.urls")),
]
