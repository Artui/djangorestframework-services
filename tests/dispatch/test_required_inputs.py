"""Dispatch-time enforcement of the ``InputRequired`` marker.

Advertising a key as required is only half the contract: models and MCP clients
omit required arguments routinely. Without enforcement the callable raises a bare
``KeyError`` from inside dispatch, which no transport maps to a caller-visible
error. These tests pin the failure to ``ServiceValidationError`` and pin *which*
channels count as satisfying the requirement.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from typing_extensions import Unpack

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.http_extras import HttpExtras
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.selector_spec import SelectorSpec


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
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert excinfo.value.detail == {
        "non_field_errors": ["Missing required argument(s): 'project_pk'."]
    }


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
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=lambda **_kw: {"project_pk": 3})
    result = dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(3, None)]


def test_a_declining_provider_does_not_satisfy_the_requirement() -> None:
    from rest_framework_services.types.unset import UNSET

    # ``UNSET`` drops the key from the pool entirely, so the requirement is
    # unmet — the marker must not be fooled by a provider that declined.
    ctx = build_offline_context(None, {})
    spec = _spec(kwargs=lambda **_kw: {"project_pk": UNSET})
    with pytest.raises(ServiceValidationError):
        dispatch_spec(spec, user=None, params={}, request=ctx.request, view=ctx.view)


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
    with pytest.raises(ServiceValidationError):
        await adispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)


async def test_async_dispatch_accepts_a_satisfied_requirement() -> None:
    ctx = build_offline_context(None, {}, kwargs={"project_pk": 4})
    result = await adispatch_spec(_spec(), user=None, params={}, request=ctx.request, view=ctx.view)
    assert result.value == [(4, None)]
