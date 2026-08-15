"""Add Show.slug, the public URL identity (ADR-03), and backfill the catalog.

Three steps so an existing, populated database migrates cleanly: add the field
nullable (no unique clash while every row is empty), backfill a unique slug for
each show from its name, then tighten to the final unique field. The backfill
resolves collisions with a numeric suffix, matching Show._unique_slug, so slugs
minted by a migration and slugs minted on save never disagree.
"""

from django.db import migrations, models
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    Show = apps.get_model("shows", "Show")
    taken = set()
    # Deterministic order so a rebuild reproduces the same slugs, and so the
    # numeric suffix lands on the same show every time.
    for show in Show.objects.order_by("pk"):
        base = slugify(show.name) or "show"
        slug = base
        n = 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        show.slug = slug
        show.save(update_fields=["slug"])


def clear_slugs(apps, schema_editor):
    Show = apps.get_model("shows", "Show")
    Show.objects.update(slug=None)


class Migration(migrations.Migration):

    dependencies = [
        ("shows", "0004_castmember_episode_count_crewmember_episode_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="slug",
            field=models.SlugField(max_length=300, null=True, blank=True),
        ),
        migrations.RunPython(backfill_slugs, clear_slugs),
        migrations.AlterField(
            model_name="show",
            name="slug",
            field=models.SlugField(max_length=300, unique=True, blank=True),
        ),
    ]
