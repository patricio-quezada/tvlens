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
    path("shows/<int:pk>/similar/", views.similar, name="similar"),
]
