"""
TVLens data models — 16 tables for a MovieLens-inspired TV recommendation platform.

Tables:
  1. Genre              9. Rating
  2. Network           10. Review
  3. Show              11. Watchlist
  4. Season            12. WatchHistory
  5. Episode           13. Tag
  6. Person            14. ShowTag
  7. CastMember        15. Recommendation
  8. CrewMember        16. UserProfile
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


# ── 1. Genre ──────────────────────────────────────────────────────────────────

class Genre(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ── 2. Network ────────────────────────────────────────────────────────────────

class Network(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    logo_path = models.CharField(max_length=500, blank=True)
    origin_country = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ── 3. Show ───────────────────────────────────────────────────────────────────

class Show(models.Model):
    class Status(models.TextChoices):
        RETURNING = "Returning Series"
        ENDED = "Ended"
        CANCELED = "Canceled"
        IN_PRODUCTION = "In Production"
        PLANNED = "Planned"

    tmdb_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=300)
    original_name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)
    tagline = models.CharField(max_length=500, blank=True)
    poster_path = models.CharField(max_length=500, blank=True)
    backdrop_path = models.CharField(max_length=500, blank=True)
    first_air_date = models.DateField(null=True, blank=True)
    last_air_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, blank=True)
    vote_average = models.FloatField(default=0)
    vote_count = models.IntegerField(default=0)
    popularity = models.FloatField(default=0)
    original_language = models.CharField(max_length=10, blank=True)
    number_of_seasons = models.IntegerField(default=0)
    number_of_episodes = models.IntegerField(default=0)
    homepage = models.URLField(max_length=500, blank=True)

    genres = models.ManyToManyField(Genre, related_name="shows", blank=True)
    networks = models.ManyToManyField(Network, related_name="shows", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity"]

    def __str__(self):
        return self.name

    @property
    def poster_url(self):
        if self.poster_path:
            return f"{settings.TMDB_IMAGE_BASE_URL}/w500{self.poster_path}"
        return ""

    @property
    def average_rating(self):
        avg = self.ratings.aggregate(models.Avg("score"))["score__avg"]
        return round(avg, 1) if avg else None


# ── 4. Season ─────────────────────────────────────────────────────────────────

class Season(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="seasons")
    tmdb_id = models.IntegerField(unique=True)
    season_number = models.IntegerField()
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=500, blank=True)
    air_date = models.DateField(null=True, blank=True)
    episode_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["show", "season_number"]
        unique_together = ["show", "season_number"]

    def __str__(self):
        return f"{self.show.name} — Season {self.season_number}"


# ── 5. Episode ────────────────────────────────────────────────────────────────

class Episode(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="episodes")
    tmdb_id = models.IntegerField(unique=True)
    episode_number = models.IntegerField()
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)
    still_path = models.CharField(max_length=500, blank=True)
    air_date = models.DateField(null=True, blank=True)
    vote_average = models.FloatField(default=0)
    vote_count = models.IntegerField(default=0)
    runtime = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["season", "episode_number"]
        unique_together = ["season", "episode_number"]

    def __str__(self):
        return f"{self.season.show.name} S{self.season.season_number:02d}E{self.episode_number:02d}"


# ── 6. Person ─────────────────────────────────────────────────────────────────

class Person(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=300)
    profile_path = models.CharField(max_length=500, blank=True)
    biography = models.TextField(blank=True)
    birthday = models.DateField(null=True, blank=True)
    place_of_birth = models.CharField(max_length=300, blank=True)
    known_for_department = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name_plural = "people"
        ordering = ["name"]

    def __str__(self):
        return self.name


# ── 7. CastMember ─────────────────────────────────────────────────────────────

class CastMember(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="cast")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="cast_roles")
    character = models.CharField(max_length=300, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = ["show", "person", "character"]

    def __str__(self):
        return f"{self.person.name} as {self.character} in {self.show.name}"


# ── 8. CrewMember ─────────────────────────────────────────────────────────────

class CrewMember(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="crew")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="crew_roles")
    job = models.CharField(max_length=200)
    department = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ["show", "person", "job"]

    def __str__(self):
        return f"{self.person.name} — {self.job} on {self.show.name}"


# ── 9. Rating ─────────────────────────────────────────────────────────────────

class Rating(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings")
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="ratings")
    score = models.FloatField(validators=[MinValueValidator(0.5), MaxValueValidator(5.0)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "show"]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username} rated {self.show.name}: {self.score}"


# ── 10. Review ────────────────────────────────────────────────────────────────

class Review(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="reviews")
    title = models.CharField(max_length=300)
    body = models.TextField()
    contains_spoilers = models.BooleanField(default=False)
    helpful_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "show"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review of {self.show.name} by {self.user.username}"


# ── 11. Watchlist ─────────────────────────────────────────────────────────────

class Watchlist(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist")
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="watchlisted_by")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "show"]
        ordering = ["-added_at"]

    def __str__(self):
        return f"{self.user.username} → {self.show.name}"


# ── 12. WatchHistory ──────────────────────────────────────────────────────────

class WatchHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watch_history")
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="watched_by")
    watched_at = models.DateTimeField(auto_now_add=True)
    completed = models.BooleanField(default=True)
    progress_seconds = models.IntegerField(default=0)

    class Meta:
        verbose_name_plural = "watch histories"
        ordering = ["-watched_at"]

    def __str__(self):
        return f"{self.user.username} watched {self.episode}"


# ── 13. Tag ───────────────────────────────────────────────────────────────────

class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ── 14. ShowTag ───────────────────────────────────────────────────────────────

class ShowTag(models.Model):
    """User-applied tags on shows, inspired by MovieLens genome tags."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="show_tags")
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="user_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="show_tags")
    relevance = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="How relevant this tag is to the show (0.0–1.0)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "show", "tag"]

    def __str__(self):
        return f"{self.tag.name} on {self.show.name} by {self.user.username}"


# ── 15. Recommendation ───────────────────────────────────────────────────────

class Recommendation(models.Model):
    class Algorithm(models.TextChoices):
        COLLABORATIVE = "collaborative", "Collaborative Filtering"
        CONTENT = "content", "Content-Based"
        HYBRID = "hybrid", "Hybrid"
        POPULAR = "popular", "Popularity-Based"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recommendations")
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="recommended_to")
    score = models.FloatField(help_text="Predicted relevance score")
    algorithm = models.CharField(max_length=20, choices=Algorithm.choices)
    reason = models.TextField(blank=True, help_text="Human-readable explanation")
    generated_at = models.DateTimeField(auto_now_add=True)
    seen = models.BooleanField(default=False)

    class Meta:
        ordering = ["-score"]
        unique_together = ["user", "show", "algorithm"]

    def __str__(self):
        return f"Rec: {self.show.name} for {self.user.username} ({self.algorithm})"


# ── 16. UserProfile ──────────────────────────────────────────────────────────

class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile of {self.user.username}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
