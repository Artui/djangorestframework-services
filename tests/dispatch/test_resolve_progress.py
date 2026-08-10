"""Tests for ``resolve_progress`` — the spec-level progress sink."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.dispatch.utils import resolve_progress
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def _service(**_: Any) -> None: ...


def _recorder(into: list[Any]) -> Any:
    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        into.append(progress)

    return report


def _resolve(spec: Any, progress: Any = None) -> Any:
    return resolve_progress(spec, progress, user=None, request=None, view=None)


def test_no_spec_reporter_passes_the_transport_reporter_through() -> None:
    seen: list[Any] = []
    transport = _recorder(seen)
    assert _resolve(ServiceSpec(service=_service), transport) is transport


def test_nothing_configured_anywhere_yields_the_no_op() -> None:
    # With three possible sinks (caller, view hook, spec) the merge always runs,
    # so this returns the no-op rather than ``None``. Equivalent for the pool —
    # ``base_pool`` would have substituted the same function — and it keeps the
    # invariant that ``resolve_progress`` always hands back something callable.
    assert _resolve(ServiceSpec(service=_service)) is null_progress


def test_both_sinks_receive_the_report() -> None:
    spec_seen: list[Any] = []
    transport_seen: list[Any] = []
    spec = ServiceSpec(service=_service, progress_reporter=lambda: _recorder(spec_seen))
    _resolve(spec, _recorder(transport_seen))(7)
    assert spec_seen == [7]
    assert transport_seen == [7]


def test_a_declining_provider_leaves_the_transport_reporter_alone() -> None:
    # ``None`` back from the provider means "nothing to report to on this run",
    # which must not degrade the transport's own sink to a no-op.
    seen: list[Any] = []
    transport = _recorder(seen)
    spec = ServiceSpec(service=_service, progress_reporter=lambda: None)
    _resolve(spec, transport)(1)
    assert seen == [1]


def test_a_declining_provider_with_no_transport_reporter_is_the_no_op() -> None:
    spec = ServiceSpec(service=_service, progress_reporter=lambda: None)
    assert _resolve(spec) is null_progress


def test_the_provider_is_invoked_through_the_keyword_pool() -> None:
    got: dict[str, Any] = {}

    def provider(*, user: Any, request: Any) -> Any:
        got["user"] = user
        got["request"] = request
        return None

    spec = ServiceSpec(service=_service, progress_reporter=provider)
    resolve_progress(spec, None, user="u", request="r", view="v")
    # Only what it declared — ``view`` was on offer and not asked for.
    assert got == {"user": "u", "request": "r"}


def test_selectors_carry_the_field_too() -> None:
    seen: list[Any] = []
    spec = SelectorSpec(kind=SelectorKind.LIST, progress_reporter=lambda: _recorder(seen))
    _resolve(spec)(3)
    assert seen == [3]
