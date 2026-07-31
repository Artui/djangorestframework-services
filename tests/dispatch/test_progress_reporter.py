"""The ``progress`` pool seed — how a long-running service reports its progress.

The seed exists so a service can be written once and dispatched anywhere. Most
of these tests are therefore about what happens when *nobody is listening*,
because that is the case that decides whether the parameter is safe to declare
at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    base_pool,
    dispatch_spec,
    null_progress,
)
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS


class _Recorder:
    """A reporter that keeps what it was told, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, str | None]] = []

    def __call__(
        self, progress: float, *, total: float | None = None, message: str | None = None
    ) -> None:
        self.calls.append((progress, total, message))


def _service(*, data: Any = None, progress: ProgressReporter) -> dict[str, Any]:
    del data
    progress(1, total=3, message="starting")
    progress(3, total=3)
    return {"ok": True}


# ----- the seed reaches the callable -----


def test_a_service_that_declares_progress_receives_the_reporter() -> None:
    recorder = _Recorder()
    dispatch_spec(
        ServiceSpec(service=_service, atomic=False),
        user=None,
        params={},
        progress=recorder,
    )
    assert recorder.calls == [(1, 3, "starting"), (3, 3, None)]


def test_a_selector_may_report_too() -> None:
    recorder = _Recorder()

    def selector(*, progress: ProgressReporter) -> list[int]:
        progress(0.5, message="halfway")
        return [1, 2]

    dispatch_spec(
        SelectorSpec(kind=SelectorKind.LIST, selector=selector),
        user=None,
        params={},
        progress=recorder,
    )
    assert recorder.calls == [(0.5, None, "halfway")]


# ----- ...and is safe when nobody is listening -----


def test_the_same_service_runs_with_no_reporter_supplied() -> None:
    """The point of the default: one service, every transport.

    Without it, declaring ``progress`` would work off-HTTP and raise a
    ``TypeError`` everywhere else, so nobody could declare it in shared code.
    """
    result = dispatch_spec(ServiceSpec(service=_service, atomic=False), user=None, params={})
    assert result.value == {"ok": True}


def test_null_progress_discards_and_returns_none() -> None:
    assert null_progress(1, total=2, message="x") is None


def test_base_pool_always_carries_a_callable_reporter() -> None:
    assert base_pool(user=None, request=None)["progress"] is null_progress
    recorder = _Recorder()
    assert base_pool(user=None, request=None, progress=recorder)["progress"] is recorder


# ----- it is a reserved seed -----


def test_progress_is_reserved() -> None:
    """A caller-supplied argument named ``progress`` must not shadow the seed.

    Same reasoning as ``request`` / ``user``: the value is the dispatcher's,
    and letting client input reach a parameter the service trusts is the
    footgun the reserved set exists to close.
    """
    assert "progress" in RESERVED_POOL_SEEDS


@pytest.mark.parametrize(
    "binding", [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]
)
def test_a_caller_supplied_progress_is_stripped_from_the_spread(
    binding: ArgumentBinding,
) -> None:
    recorder = _Recorder()

    def selector(*, progress: ProgressReporter) -> list[int]:
        progress(1)
        return []

    dispatch_spec(
        SelectorSpec(kind=SelectorKind.LIST, selector=selector),
        user=None,
        params={"progress": "spoofed"},
        argument_binding=binding,
        progress=recorder,
    )
    # The dispatcher's reporter ran; the string never reached the parameter.
    assert recorder.calls == [(1, None, None)]


# ----- the seed is not MCP-shaped: any transport can supply one -----


@pytest.mark.django_db
def test_an_http_view_can_supply_a_reporter_through_its_kwargs_hook() -> None:
    """The general case, and the reason this is a *pool seed* rather than a
    parameter of one dispatcher.

    A project tracking a long-running job — a Celery task writing to a workflow
    model, a view pushing to a websocket — supplies its own reporter through the
    same ``get_service_kwargs`` hook it already uses for tenants and clocks. The
    extras merge over the seeds, so a server-authored ``progress`` replaces the
    no-op. (Client *input* named ``progress`` cannot: it is a reserved seed and
    is stripped from the spread, which is the asymmetry that makes this safe.)
    """
    from rest_framework.test import APIRequestFactory

    from rest_framework_services import ServiceViewSet

    seen: list[tuple[float, float | None, str | None]] = []

    def slow_export(*, progress: ProgressReporter) -> dict[str, Any]:
        progress(1, total=2, message="working")
        return {"ok": True}

    class _View(ServiceViewSet):
        action_specs = {"create": ServiceSpec(service=slow_export)}

        def get_service_kwargs(self) -> dict[str, Any]:
            # Whatever this project already has: a websocket group send, a
            # workflow-model row, a Celery `update_state`.
            return {
                "progress": lambda p, *, total=None, message=None: seen.append((p, total, message))
            }

    request = APIRequestFactory().post("/", {}, format="json")
    response = _View.as_view({"post": "create"})(request)
    assert response.status_code == 201, response.data
    assert seen == [(1, 2, "working")]


def test_the_reporter_is_sync_even_when_the_sink_is_not() -> None:
    """⚠ The first wall a websocket consumer hits.

    A reporter is called from inside domain code that is written once for both
    transports and is therefore never ``async def`` — so an async sink is
    bridged at the reporter, not at the service. Same rule the rest of the
    package follows for spec callables.
    """
    from asgiref.sync import async_to_sync

    sent: list[str] = []

    async def push(text: str) -> None:
        sent.append(text)

    def reporter(
        progress: float, *, total: float | None = None, message: str | None = None
    ) -> None:
        async_to_sync(push)(f"{progress}/{total}: {message}")

    dispatch_spec(
        ServiceSpec(service=_service, atomic=False), user=None, params={}, progress=reporter
    )
    assert sent == ["1/3: starting", "3/3: None"]
