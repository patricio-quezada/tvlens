"""Shows app URL configuration."""

from django.urls import path

from . import views

app_name = "shows"

urlpatterns = [
    path("", views.index, name="index"),
    path("changelog/", views.changelog, name="changelog"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("credits/", views.credits, name="credits"),
    path("search/", views.search, name="search"),
    path("search/request/", views.request_show, name="request_show"),
    path("genre/<int:pk>/", views.genre, name="genre"),
    path("my-ratings/", views.my_ratings, name="my_ratings"),
    path("shows/<slug:slug>/", views.detail, name="detail"),
    path("shows/<slug:slug>/rate/", views.rate, name="rate"),
    path("shows/<slug:slug>/tag/", views.add_tag, name="add_tag"),
    path("shows/<slug:slug>/untag/", views.remove_tag, name="remove_tag"),
    path("tags/<slug:slug>/", views.tag, name="tag"),
]
