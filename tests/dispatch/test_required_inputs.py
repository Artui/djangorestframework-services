"""Dispatch-time enforcement of the ``InputRequired`` marker.

Advertising a key as required is only half the contract: models and MCP clients
omit required arguments routinely. Without enforcement the callable raises a bare
``KeyError`` from inside dispatch, which no transport maps to a caller-visible
error. These tests pin the failure to a validation error keyed by the missing
argument, and pin *which* channels count as satisfying the requirement.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from rest_framework.exceptions import ValidationError
from typing_extensions import TypedDict, Unpack

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.types.http_extras import HttpExtras
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.unset import UNSET


class _WidgetExtras(HttpExtras[Any], total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: str


def _list_widgets(**extras: Unpack[_WidgetExtras]) -> list[Any]:
    """List widgets in a project."""
    return [(extras["project_pk"], extras.get("team_role"))]


def _spec(**kwargs: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(selector=_list_widgets, kind="list", **kwargs)


def _dispatch(spec: SelectorSpec[Any, Any], **context: Any) -> Any:
    ctx = build_offline_context(None, context.pop("params", {}) or {}, **context)
    return dispatch_spec(
        spec, user=None, params=context.get("params", {}) or {}, request=ctx.request, view=ctx.view
    )


def test_missing_required_key_raises_service_validation_error() -> None:
    ctx = build_offline_context(None, {})
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}
    assert excinfo.value.get_codes() == {"project_pk": ["required"]}


def test_params_satisfy_the_requirement() -> None:
    ctx = build_offline_context(None, {"project_pk": 7})
    result = dispatch_spec(
        _spec(), user=None, params={"project_pk": 7}, request=ctx.request, view=ctx.view
    )
    assert result.value == [(7, None)]


def test_url_kwargs_channel_satisfies_the_requirement() -> None:
    # The key never appears in ``params`` — ``build_offline_context(kwargs=…)``
    # is the channel a registered ``UrlKwarg`` routes through.
    ctx = build_offline_context(None, {}, kwargs={"project_pk": 9})
    result = dispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(9, None)]


def test_a_provider_satisfies_the_requirement() -> None:
    """An untyped provider, as drfs has always taken: the edge calls it before
    the shape check, so the key it answers is there to be checked."""
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=lambda **_kw: {"project_pk": 3})
    result = dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(3, None)]


class _FillsTheProject(TypedDict):
    project_pk: int


def _fill_the_project(**_kw: Any) -> _FillsTheProject:
    return {"project_pk": 3}


def _decline_the_project(**_kw: Any) -> _FillsTheProject:
    return {"project_pk": UNSET}


def test_a_typed_provider_satisfies_the_requirement() -> None:
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=_fill_the_project)
    result = dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(3, None)]


def test_a_typed_provider_declining_a_required_key_is_refused_as_missing() -> None:
    """Refused as the untyped provider's declined key is, never a bare ``KeyError``."""
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=_decline_the_project)
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}


def test_a_provider_runs_once_per_call() -> None:
    calls: list[int] = []

    def provide(**_kw: Any) -> dict[str, Any]:
        calls.append(1)
        return {"project_pk": 3}

    ctx = build_offline_context(None, {})
    dispatch_spec(_spec(kwargs=provide), user=None, params={}, request=ctx.request, view=ctx.view)
    assert calls == [1]


def test_a_declining_provider_does_not_satisfy_the_requirement() -> None:
    # ``UNSET`` drops the key from the pool entirely, so the requirement is
    # unmet: the marker must not be fooled by a provider that declined. The
    # exact refusal is what holds the decline: an ``UNSET`` handed on as the
    # value would be refused as the wrong type instead.
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=lambda **_kw: {"project_pk": UNSET})
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}


def test_unmarked_spec_is_unaffected() -> None:
    def plain(**extras: Any) -> list[Any]:
        return [extras.get("anything")]

    ctx = build_offline_context(None, {})
    spec = SelectorSpec(selector=plain, kind="list")
    assert dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view).value == [
        None
    ]


async def test_async_dispatch_enforces_the_same_requirement() -> None:
    ctx = build_offline_context(None, {})
    with pytest.raises(ValidationError) as excinfo:
        await adispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}


async def test_async_dispatch_accepts_a_satisfied_requirement() -> None:
    ctx = build_offline_context(None, {}, kwargs={"project_pk": 4})
    result = await adispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(4, None)]


def _none_defaulted(*, project_pk: Annotated[int | None, InputRequired] = None) -> list[Any]:
    return [project_pk]


# Python 3.10's ``typing.get_type_hints`` wraps a ``None``-defaulted parameter in
# ``Optional[...]``, hiding the marker: the selector then ran with the default.


def test_a_none_defaulted_required_key_is_enforced() -> None:
    ctx = build_offline_context(None, {})
    spec = SelectorSpec(selector=_none_defaulted, kind="list")
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}


async def test_async_dispatch_enforces_a_none_defaulted_required_key() -> None:
    ctx = build_offline_context(None, {})
    spec = SelectorSpec(selector=_none_defaulted, kind="list")
    with pytest.raises(ValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {"project_pk": ["This field is required."]}
