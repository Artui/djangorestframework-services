"""``base_pool`` is django-service-specs' own; its behaviour is tested there.

Kept here: its identity at drfs' paths, and the two ways it answers differently
from the function drfs defined before it, each of which a drfs caller can meet.
"""

from __future__ import annotations

from typing import Any

from django_service_specs.pool.base_pool import base_pool as kernel_base_pool

import rest_framework_services
from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.null_progress import null_progress


def test_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert base_pool is kernel_base_pool
    assert rest_framework_services.base_pool is kernel_base_pool


def test_a_pool_needs_no_request() -> None:
    """``request`` is a transport's entry, not a seed every pool carries."""
    user = object()

    assert base_pool(user=user) == {"user": user, "progress": null_progress}


def test_a_request_passed_still_lands_in_the_pool() -> None:
    """Through ``**extra``, so a caller that has always passed one keeps its key."""
    assert base_pool(user="u", request="r")["request"] == "r"


class _Recorder(list[Any]):
    """A list-backed reporter: falsy until the first call records something."""

    def __call__(self, progress: float, *, total: float | None = None, **rest: Any) -> None:
        self.append(progress)


def test_a_falsy_reporter_is_kept_rather_than_swapped_for_the_no_op() -> None:
    recorder = _Recorder()
    assert not recorder

    # ``request=`` only so the call is one the old signature accepted too, and the
    # assertion below is the one that answers.
    assert base_pool(user="u", request="r", progress=recorder)["progress"] is recorder
