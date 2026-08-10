"""Tests for ``resolve_progress_hook`` — the view's own progress sink."""

from __future__ import annotations

from typing import Any

from rest_framework_services.views.utils import resolve_progress_hook


class _NoHooks:
    """A plain view — the overwhelmingly common case."""


def _sink(_: Any) -> None: ...


def test_a_view_offering_nothing_yields_none() -> None:
    # Which leaves the seed as ``null_progress``: opting out is the default and
    # costs a view exactly nothing.
    assert resolve_progress_hook(_NoHooks(), None, action="create") is None


def test_the_catch_all_hook_is_used() -> None:
    class View:
        def get_progress_reporter(self) -> Any:
            return _sink

    assert resolve_progress_hook(View(), None, action="create") is _sink


def test_the_per_action_hook_wins_over_the_catch_all() -> None:
    other: Any = lambda _: None  # noqa: E731

    class View:
        def get_create_progress_reporter(self) -> Any:
            return _sink

        def get_progress_reporter(self) -> Any:
            return other

    assert resolve_progress_hook(View(), None, action="create") is _sink


def test_a_per_action_hook_returning_none_falls_through_to_the_catch_all() -> None:
    # Declining is not the same as having no opinion: an action-specific hook
    # that returns ``None`` for *this* run should not suppress the view-wide
    # sink, or a per-action override becomes a per-action off switch.
    class View:
        def get_create_progress_reporter(self) -> Any:
            return None

        def get_progress_reporter(self) -> Any:
            return _sink

    assert resolve_progress_hook(View(), None, action="create") is _sink


def test_no_action_skips_the_per_action_lookup() -> None:
    # Standalone single-purpose views have no ``action``; only the catch-all
    # applies, and no attribute named ``get_None_progress_reporter`` is sought.
    class View:
        def get_progress_reporter(self) -> Any:
            return _sink

    assert resolve_progress_hook(View(), None, action=None) is _sink


def test_the_hook_is_invoked_through_the_keyword_pool() -> None:
    got: dict[str, Any] = {}

    class View:
        def get_progress_reporter(self, *, request: Any) -> Any:
            got["request"] = request
            return _sink

    assert resolve_progress_hook(View(), "req", action=None) is _sink
    # Declared ``request`` only; ``view`` was on offer and not taken.
    assert got == {"request": "req"}
