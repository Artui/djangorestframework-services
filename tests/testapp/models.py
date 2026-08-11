"""Test models exercising the full surface area of the mutation helpers."""

from __future__ import annotations

from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = "testapp"


class Author(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class Timestamped(models.Model):
    title = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "testapp"


class Post(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(default="")
    published = models.BooleanField(default=False)
    views = models.IntegerField(default=0)
    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="posts",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts")

    class Meta:
        app_label = "testapp"


# --- nested-write (NEST) fixtures ----------------------------------------
#
# Catalog ──< Section ──< Item        (non-nullable FKs → orphans DELETE)
#         └─< Note                     (nullable FK     → orphans UNLINK)
# Section.tags is an m2m so a ChildSpec.m2m callback can be exercised.


class Catalog(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class Section(models.Model):
    catalog = models.ForeignKey(Catalog, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, blank=True, related_name="sections")

    class Meta:
        app_label = "testapp"


class Item(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="items")
    label = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class Note(models.Model):
    catalog = models.ForeignKey(
        Catalog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notes",
    )
    body = models.CharField(max_length=200)

    class Meta:
        app_label = "testapp"


# --- singular relation fixtures ------------------------------------------
#
# Post.author       forward FK,   nullable  → may be cleared
# Profile.author    forward O2O,  nullable  → and Author.profile in reverse,
#                                             whose nullable FK unlinks
# Cover.catalog     non-nullable  → Catalog.cover in reverse deletes instead


class Profile(models.Model):
    author = models.OneToOneField(
        Author,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profile",
    )
    bio = models.CharField(max_length=200, default="")

    class Meta:
        app_label = "testapp"


class Cover(models.Model):
    catalog = models.OneToOneField(Catalog, on_delete=models.CASCADE, related_name="cover")
    image = models.CharField(max_length=200, default="")

    class Meta:
        app_label = "testapp"
