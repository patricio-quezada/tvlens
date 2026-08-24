"""Shows app URL configuration."""

from django.urls import path

from . import views

app_name = "shows"

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search, name="search"),
    path("genre/<int:pk>/", views.genre, name="genre"),
    path("my-ratings/", views.my_ratings, name="my_ratings"),
    path("shows/<slug:slug>/", views.detail, name="detail"),
    path("shows/<slug:slug>/rate/", views.rate, name="rate"),
    path("shows/<slug:slug>/tag/", views.add_tag, name="add_tag"),
    path("shows/<slug:slug>/untag/", views.remove_tag, name="remove_tag"),
    path("tags/<slug:slug>/", views.tag, name="tag"),
    path("shows/<int:pk>/similar/", views.similar, name="similar"),
]
