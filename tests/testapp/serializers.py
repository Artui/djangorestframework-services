"""Serializers used by the view/viewset tests."""

from __future__ import annotations

from rest_framework import serializers

from tests.testapp.models import Author, Post, Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name")


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ("id", "name")


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title", "body", "published", "views", "author")
