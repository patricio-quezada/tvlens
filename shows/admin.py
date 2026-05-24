"""TVLens admin configuration."""

from django.contrib import admin

from .models import (
    CastMember,
    CrewMember,
    Episode,
    Genre,
    Network,
    Person,
    Rating,
    Recommendation,
    Review,
    Season,
    Show,
    ShowTag,
    Tag,
    UserProfile,
    WatchHistory,
    Watchlist,
)


# ── Inlines ───────────────────────────────────────────────────────────────────

class SeasonInline(admin.TabularInline):
    model = Season
    extra = 0
    fields = ("season_number", "name", "air_date", "episode_count")
    readonly_fields = ("tmdb_id",)


class CastInline(admin.TabularInline):
    model = CastMember
    extra = 0
    autocomplete_fields = ("person",)


class CrewInline(admin.TabularInline):
    model = CrewMember
    extra = 0
    autocomplete_fields = ("person",)


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ("episode_number", "name", "air_date", "vote_average")


# ── Model Admins ──────────────────────────────────────────────────────────────

@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "tmdb_id")
    search_fields = ("name",)


@admin.register(Network)
class NetworkAdmin(admin.ModelAdmin):
    list_display = ("name", "origin_country", "tmdb_id")
    search_fields = ("name",)
    list_filter = ("origin_country",)


@admin.register(Show)
class ShowAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "first_air_date", "vote_average", "popularity")
    list_filter = ("status", "genres")
    search_fields = ("name", "original_name")
    filter_horizontal = ("genres", "networks")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SeasonInline, CastInline, CrewInline]
    date_hierarchy = "first_air_date"


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("__str__", "air_date", "episode_count")
    list_filter = ("show",)
    inlines = [EpisodeInline]


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("__str__", "air_date", "vote_average", "runtime")
    list_filter = ("season__show",)
    search_fields = ("name",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("name", "known_for_department", "birthday")
    search_fields = ("name",)
    list_filter = ("known_for_department",)


@admin.register(CastMember)
class CastMemberAdmin(admin.ModelAdmin):
    list_display = ("person", "character", "show", "order")
    autocomplete_fields = ("show", "person")


@admin.register(CrewMember)
class CrewMemberAdmin(admin.ModelAdmin):
    list_display = ("person", "job", "department", "show")
    list_filter = ("department",)
    autocomplete_fields = ("show", "person")


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("user", "show", "score", "updated_at")
    list_filter = ("score",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "show", "title", "contains_spoilers", "helpful_count", "created_at")
    list_filter = ("contains_spoilers",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("user", "show", "priority", "added_at")
    list_filter = ("priority",)


@admin.register(WatchHistory)
class WatchHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "episode", "watched_at", "completed")
    list_filter = ("completed",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ShowTag)
class ShowTagAdmin(admin.ModelAdmin):
    list_display = ("tag", "show", "user", "relevance", "created_at")
    list_filter = ("tag",)


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("user", "show", "score", "algorithm", "seen", "generated_at")
    list_filter = ("algorithm", "seen")
    readonly_fields = ("generated_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "joined_at")
    search_fields = ("user__username",)
    readonly_fields = ("joined_at",)
