"""
TVLens data models — 17 tables for a MovieLens-inspired TV recommendation platform.

Tables:
  1. Genre              9. Rating           17. SimilarShow
  2. Network           10. Review
  3. Show              11. Watchlist
  4. Season            12. WatchHistory
  5. Episode           13. Tag
  6. Person            14. ShowTag
  7. CastMember
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


class ShowQuerySet(models.QuerySet):
    def watched_by(self, user):
        """Shows this user has watched, with rating-implies-watched (ADR-08).

        Watched means the user has either rated the show or logged any
        WatchHistory for one of its episodes. A rating counts on its own, with
        no episode row, because you cannot rate what you have not seen (ADR-08).
        Layer 2 (#6) reads this to know which shows a user has engaged with,
        alongside the ratings themselves.

        Derived, not stored: watched is a pure function of Rating and
        WatchHistory, so a separate "watched" column would only be a second
        source of truth to drift out of sync. The OR-join can repeat a show
        once per matching episode, so distinct() collapses it back to one row.
        """
        if not user.is_authenticated:
            return self.none()
        return self.filter(
            models.Q(ratings__user=user) | models.Q(seasons__episodes__watched_by__user=user)
        ).distinct()


class Show(models.Model):
    class Status(models.TextChoices):
        RETURNING = "Returning Series"
        ENDED = "Ended"
        CANCELED = "Canceled"
        IN_PRODUCTION = "In Production"
        PLANNED = "Planned"

    tmdb_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=300)
    # Public identity in the URL (ADR-03). Stable once set: a re-import that
    # changes the name keeps the old slug so links do not rot. unique=True
    # carries its own index. Long because a show name can be, though slugs
    # in the catalog are short.
    slug = models.SlugField(max_length=300, unique=True, blank=True)
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
    trailer_key = models.CharField(max_length=40, blank=True)

    genres = models.ManyToManyField(Genre, related_name="shows", blank=True)
    networks = models.ManyToManyField(Network, related_name="shows", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ShowQuerySet.as_manager()

    class Meta:
        # Alphabetical, deliberately. The default used to be ["-popularity"],
        # which meant any queryset that forgot an explicit order_by silently
        # became a popularity chart, the one thing this recommender exists to
        # avoid (ADR-05). It failed quietly and in the wrong direction. Name is
        # deterministic and carries no signal; every row that wants an order
        # states it (see views.index and shows/personalization.py).
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Fill the slug on first save so ingestion keeps working without
        # touching its upsert, and keep it stable afterward (see the field).
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        """A slugified name, with -2, -3, ... appended on collision.

        The data migration that backfills the existing catalog resolves
        collisions the same way, so a show minted here and one minted there
        never disagree.
        """
        from django.utils.text import slugify

        base = slugify(self.name) or "show"
        slug = base
        n = 2
        taken = Show.objects.exclude(pk=self.pk)
        while taken.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("shows:detail", kwargs={"slug": self.slug})

    @property
    def poster_url(self):
        if self.poster_path:
            return f"{settings.TMDB_IMAGE_BASE_URL}/w342{self.poster_path}"
        return ""

    @property
    def backdrop_url(self):
        if self.backdrop_path:
            return f"{settings.TMDB_IMAGE_BASE_URL}/w1280{self.backdrop_path}"
        return ""

    @property
    def trailer_url(self):
        """YouTube watch URL, or empty when TMDb had no usable video."""
        return f"https://www.youtube.com/watch?v={self.trailer_key}" if self.trailer_key else ""

    @property
    def tmdb_score_5(self):
        """TMDb's community score on TVLens's own 0.5-5 scale.

        TMDb rates 0-10 and TVLens rates 0.5-5 in half steps. Rendering both
        behind the same star put an 8.4 next to a user's 4.5 on one page, which
        reads as dislike rather than as two scales (#19). Nothing displays
        vote_average raw any more.

        This is not a new conversion: personalization.top_picks already divides
        vote_average by 2 for its cold-start baseline, so the repo had already
        settled what a TMDb point is worth in TVLens stars. This makes that
        conversion visible instead of leaving it buried in the ranking.

        None when nothing has voted, which the templates render as a dash.
        """
        if not self.vote_count:
            return None
        return self.vote_average / 2

    @property
    def average_rating(self):
        avg = self.ratings.aggregate(models.Avg("score"))["score__avg"]
        return round(avg, 1) if avg else None

    def is_watched_by(self, user):
        """Whether this one show counts as watched for user (ShowQuerySet.watched_by).

        Kept as the single definition of the rule so the per-show check and the
        bulk queryset can never disagree about what "watched" means.
        """
        if not user.is_authenticated:
            return False
        return Show.objects.watched_by(user).filter(pk=self.pk).exists()


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
    episode_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Episodes this person appeared in. Null means a series-level "
        "credit with no episode rollup.",
    )

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
    episode_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Episodes this person worked on. Null means a series-level "
        "credit with no episode rollup.",
    )

    class Meta:
        unique_together = ["show", "person", "job"]

    def __str__(self):
        return f"{self.person.name} — {self.job} on {self.show.name}"


# ── 9. Rating ─────────────────────────────────────────────────────────────────


class Rating(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ratings",
    )
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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watchlist",
    )
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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watch_history",
    )
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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="show_tags",
    )
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


# ── 17. SimilarShow ───────────────────────────────────────────────────────────


class SimilarShow(models.Model):
    """One precomputed Layer 1 edge: source -> target, at a fixed rank.

    This is the materialized shared-people graph. similar_by_people(show) is a
    pure function of the catalog (same answer for everyone, changes only on
    ingest), yet it runs a Python-scored traversal on every request. We compute
    it once and serve reads from this table instead. The whole table is rebuilt
    wholesale after every ingest; nothing writes here per request.

    mode is the source's rung on the fallback ladder (weighted/estimated/rating,
    ADR-05). Every edge of one source shares it, so it is denormalized onto each
    row rather than kept in a second table: one table is the simplest thing that
    round-trips the RankedShows shape similar_by_people returns.

    See docs/adr/07-materialized-recommendations.md.
    """

    source = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="similar_edges")
    target = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="similar_edges_in")
    rank = models.PositiveIntegerField(help_text="0-based position in the source's ranked list.")
    score = models.FloatField()
    shared_people = models.PositiveIntegerField()
    mode = models.CharField(
        max_length=10,
        help_text="The source's ladder rung: weighted, estimated, or rating.",
    )

    class Meta:
        unique_together = ["source", "target"]
        ordering = ["source", "rank"]
        indexes = [models.Index(fields=["source", "rank"])]

    def __str__(self):
        return f"{self.source.name} -> {self.target.name} (#{self.rank})"
