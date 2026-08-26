"""Tests for ``base_pool``."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.null_progress import null_progress


def test_seeds_are_the_documented_three() -> None:
    request = object()
    user = object()
    assert base_pool(user=user, request=request) == {
        "request": request,
        "user": user,
        "progress": null_progress,
    }


def test_extra_entries_join_the_seeds() -> None:
    """An adapter can build its whole pool here instead of restating the seeds."""
    own_entries: dict[str, Any] = {"tenant": "acme", "trace_id": 7}
    pool = base_pool(user="u", request="r", **own_entries)
    assert pool == {
        "request": "r",
        "user": "u",
        "progress": null_progress,
        "tenant": "acme",
        "trace_id": 7,
    }


def test_an_entry_named_after_a_seed_collides_loudly() -> None:
    """The reason to route adapter entries through here rather than a dict literal.

    A literal would let the entry outrank the transport's authenticated value in
    silence; spreading it into this call cannot.
    """
    own_entries: dict[str, Any] = {"user": "spoofed"}
    with pytest.raises(TypeError):
        base_pool(user="real", request="r", **own_entries)
