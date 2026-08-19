"""Shows app URL configuration."""

from django.urls import path

from . import views

app_name = "shows"

urlpatterns = [
    path("", views.index, name="index"),
    path("genre/<int:pk>/", views.genre, name="genre"),
    path("shows/<slug:slug>/", views.detail, name="detail"),
    path("shows/<slug:slug>/rate/", views.rate, name="rate"),
    path("shows/<int:pk>/similar/", views.similar, name="similar"),
]
