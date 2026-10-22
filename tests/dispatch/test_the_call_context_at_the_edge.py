"""The call's context through ``dispatch_spec`` and ``adispatch_spec``.

The edge takes ``call_context=``, creates a dict where the caller passes none,
and fills the four standard entries, ``request``, ``view``, ``format`` and
``action``, with ``setdefault``, so a caller's own entries win. The one dict reaches every stage
naming it: the callables the edge calls itself (the spec's ``kwargs`` provider
and ``input_data``), the kernel's target lookup and the kernel's ``dispatch``.
Each case runs through both cores, since each hands the kernel its own call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
    render_spec_output,
)
from rest_framework_services.dispatch.utils import resolve_output_context
from rest_framework_services.types.offline_service_view import OfflineServiceView
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    kwargs.setdefault("params", {})
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    kwargs.setdefault("params", {})
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])

_REQUEST = object()


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _seen_by(seen: dict[str, Any], stage: str) -> Callable[..., Any]:
    """A callable naming ``call_context`` that records the dict it was handed."""

    def record(*, call_context: dict[str, Any]) -> dict[str, Any]:
        seen[stage] = call_context
        return {}

    return record


@pytest.mark.django_db
@_CORES
def test_the_edge_fills_the_standard_entries_for_a_kwargs_provider(dispatch: Dispatch) -> None:
    # Exactly the four entries: deleting the ``setdefault`` of any one of them
    # fails here, and the edge writes nothing else.
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=lambda: None, kwargs=_seen_by(seen, "kwargs"), atomic=False)

    dispatch(spec, request=_REQUEST)

    assert seen["kwargs"] == {"request": _REQUEST, "view": None, "format": None, "action": None}


@pytest.mark.django_db
@_CORES
def test_the_edge_fills_the_standard_entries_for_input_data(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None: ...

    spec = ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        input_data=_seen_by(seen, "input_data"),
        atomic=False,
    )

    dispatch(spec, params={"title": "t"}, request=_REQUEST)

    assert seen["input_data"] == {"request": _REQUEST, "view": None, "format": None, "action": None}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(("action", "expected"), [("publish", "publish"), ("", None), (None, None)])
def test_the_action_is_the_view_s_where_it_has_one(
    dispatch: Dispatch, action: str | None, expected: str | None
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=lambda: None, kwargs=_seen_by(seen, "kwargs"), atomic=False)
    view = OfflineServiceView(request=None, action=action)

    dispatch(spec, view=view)

    assert seen["kwargs"]["view"] is view
    assert seen["kwargs"]["action"] == expected


@pytest.mark.django_db
@_CORES
def test_a_caller_s_own_entries_win(dispatch: Dispatch) -> None:
    # ``setdefault``, not assignment: each of the four is the caller's where the
    # caller put one, whatever the edge was handed.
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=lambda: None, kwargs=_seen_by(seen, "kwargs"), atomic=False)
    mine = {"request": "mine", "view": "my-view", "format": "json", "action": "mine", "trace": 7}

    dispatch(
        spec,
        request=_REQUEST,
        view=OfflineServiceView(request=None, action="publish"),
        call_context=mine,
    )

    assert seen["kwargs"] is mine
    assert mine == {
        "request": "mine",
        "view": "my-view",
        "format": "json",
        "action": "mine",
        "trace": 7,
    }


@pytest.mark.django_db
@_CORES
def test_an_empty_dict_passed_is_the_one_every_stage_is_handed(dispatch: Dispatch) -> None:
    # An empty dict is the caller's dict, not a missing one: a falsy test in
    # place of ``is None`` hands the stages a dict of the edge's own.
    seen: dict[str, Any] = {}

    def service(*, data: Any, call_context: dict[str, Any]) -> None:
        seen["service"] = call_context

    spec = ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        kwargs=_seen_by(seen, "kwargs"),
        input_data=_seen_by(seen, "input_data"),
        atomic=False,
    )
    context: dict[str, Any] = {}

    dispatch(spec, params={"title": "t"}, call_context=context)

    assert set(seen) == {"service", "kwargs", "input_data"}
    assert all(handed is context for handed in seen.values())
    assert context == {"request": None, "view": None, "format": None, "action": None}


@pytest.mark.django_db
@_CORES
def test_a_target_lookup_and_the_service_share_the_dict(dispatch: Dispatch) -> None:
    post = Post.objects.create(title="a")
    seen: dict[str, Any] = {}

    def by_pk(*, pk: int, call_context: dict[str, Any]) -> QuerySet[Post]:
        seen["lookup"] = call_context
        return Post.objects.filter(pk=pk)

    def service(*, instance: Post, call_context: dict[str, Any]) -> Post:
        seen["service"] = call_context
        return instance

    spec = ServiceSpec(
        service=service,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=by_pk),
        atomic=False,
    )
    context: dict[str, Any] = {}

    result = dispatch(spec, params={"pk": post.pk}, call_context=context)

    assert result.value == post
    assert seen["lookup"] is context
    assert seen["service"] is context


@pytest.mark.django_db
@_CORES
def test_a_selector_spec_s_selector_and_provider_share_the_dict(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def posts(*, call_context: dict[str, Any]) -> QuerySet[Post]:
        seen["selector"] = call_context
        return Post.objects.all()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=posts, kwargs=_seen_by(seen, "kwargs"))
    context: dict[str, Any] = {}

    dispatch(spec, call_context=context)

    assert seen["selector"] is context
    assert seen["kwargs"] is context


@pytest.mark.django_db
@_CORES
def test_a_many_service_s_stages_share_the_dict(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any, call_context: dict[str, Any]) -> list[Any]:
        seen["service"] = call_context
        return []

    spec = ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        many=True,
        kwargs=_seen_by(seen, "kwargs"),
        input_data=_seen_by(seen, "input_data"),
        atomic=False,
    )
    context: dict[str, Any] = {}

    dispatch(spec, params=[{"title": "t"}], call_context=context)

    assert set(seen) == {"service", "kwargs", "input_data"}
    assert all(handed is context for handed in seen.values())


@pytest.mark.django_db
@_CORES
def test_each_call_with_none_passed_has_a_dict_of_its_own(dispatch: Dispatch) -> None:
    handed: list[dict[str, Any]] = []

    def service(*, call_context: dict[str, Any]) -> None:
        call_context["written"] = len(handed)
        handed.append(call_context)

    spec = ServiceSpec(service=service, atomic=False)

    dispatch(spec)
    dispatch(spec)

    assert handed[0] is not handed[1]
    assert handed[1]["written"] == 1


# --- the output serializer context ----------------------------------------
#
# The output serializer's context provider is called by the render step, after
# the call, from a pool the edge builds. It offers the name the kernel offers,
# so a provider naming ``call_context`` is answered here as on the kernel's route.


class _PostOut(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


def _rendered_with_provider(seen: dict[str, Any]) -> SelectorSpec[Any, Any]:
    def provider(*, call_context: dict[str, Any]) -> dict[str, Any]:
        seen["provider"] = call_context
        return {}

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.all(),
        output_serializer=_PostOut,
        output_serializer_context=provider,
    )


def test_the_output_context_provider_is_handed_the_dict_passed() -> None:
    seen: dict[str, Any] = {}
    context: dict[str, Any] = {}

    resolve_output_context(
        _rendered_with_provider(seen),
        view=None,
        request=None,
        extras={},
        call_context=context,
    )

    assert seen["provider"] is context


@pytest.mark.django_db
def test_the_output_context_provider_has_a_dict_with_none_passed() -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")

    payload = render_spec_output(_rendered_with_provider(seen), post)

    assert payload == {"id": post.pk, "title": "a"}
    assert seen["provider"] == {}
