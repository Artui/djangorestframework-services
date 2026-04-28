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
