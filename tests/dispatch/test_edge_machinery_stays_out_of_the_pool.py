"""What the DRF edge carries for its own machinery never reaches a spec's callables.

The edge hands the kernel a target guard for a selector spec, the view's
serializer context layers, the author values that outrank a Validator, and the
data the input serializer binds. None of them is an argument or a seed of the
call: a callable taking a bare ``**kwargs`` is handed the call's pool, and on
main that pool held ``progress``, ``request``, ``serializer`` and ``user`` and
the call's extras, never one of these. Each test runs through both cores, and
the HTTP views are checked through a real view.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.grant import Grant
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.dispatch.utils import guarded_selector
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from tests.testapp.models import Post

_factory = APIRequestFactory()
SEEN: dict[str, list[str]] = {}


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _private(names: list[str]) -> list[str]:
    return [name for name in names if name.startswith("_")]


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _service(**rest: Any) -> None:
    SEEN["service"] = sorted(rest)


def _precondition(**rest: Any) -> None:
    SEEN["precondition"] = sorted(rest)


def _context(**rest: Any) -> dict[str, Any]:
    SEEN["context"] = sorted(rest)
    return {}


_SERVICE = ServiceSpec(
    service=_service,
    input_serializer=_TitleIn,
    input_serializer_context=_context,
    preconditions=(_precondition,),
    atomic=False,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


@_CORES
def test_a_bare_kwargs_service_sees_no_edge_machinery(dispatch: Dispatch) -> None:
    dispatch(
        _SERVICE,
        params={"title": "t"},
        view_hooks=ViewHooks(
            extra_kwargs={"tenant": "server"},
            input_serializer_context={"layer": 1},
            output_serializer_context=lambda value: {"layer": 2},
        ),
    )

    assert _private(SEEN["service"]) == []
    assert {"tenant", "request", "user", "serializer"} <= set(SEEN["service"])
    assert _private(SEEN["precondition"]) == []
    assert _private(SEEN["context"]) == []


def _rows(**rest: Any) -> QuerySet[Post]:
    SEEN["selector"] = sorted(rest)
    return Post.objects.all()


def _row(*, pk: int, **rest: Any) -> QuerySet[Post]:
    SEEN["selector"] = sorted(rest)
    return Post.objects.filter(pk=pk)


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    ("kind", "selector", "by_pk"),
    [(SelectorKind.LIST, _rows, False), (SelectorKind.RETRIEVE, _row, True)],
    ids=["list", "retrieve"],
)
def test_a_bare_kwargs_selector_sees_no_target_guard(
    dispatch: Dispatch, kind: SelectorKind, selector: Any, by_pk: bool
) -> None:
    post = Post.objects.create(title="p")
    guarded: list[Any] = []
    spec = SelectorSpec(kind=kind, selector=selector, preconditions=(_precondition,))

    dispatch(
        spec,
        params={"pk": post.pk} if by_pk else {},
        on_target_resolved=lambda _spec, _context, *, instance: guarded.append(instance),
        view_hooks=ViewHooks(output_serializer_context=lambda value: {"layer": 2}),
    )

    assert len(guarded) == 1
    assert _private(SEEN["selector"]) == []
    assert _private(SEEN["precondition"]) == []


class _View(ServiceCreateView):
    spec = _SERVICE

    def get_service_kwargs(self) -> dict[str, Any]:
        return {"tenant": "server"}

    def get_input_serializer_context(self) -> dict[str, Any]:
        return {"layer": 1}


def test_over_http_a_bare_kwargs_service_sees_no_edge_machinery() -> None:
    response = _View.as_view()(_factory.post("/", {"title": "t"}, format="json"))

    assert response.status_code == 204
    assert _private(SEEN["service"]) == []
    assert _private(SEEN["context"]) == []


@pytest.mark.django_db
def test_a_guarded_twin_dispatched_without_its_guard_is_refused() -> None:
    """The guard is read from the edge call, so a twin dispatched outside one fails closed."""
    Post.objects.create(title="p")
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_rows)
    twin = guarded_selector(kernel_spec_variant(spec, unguarded=True, server_input=False))

    with pytest.raises(ImproperlyConfigured, match="outside the call that guards it"):
        principal = AnonymousUser()
        dispatch(twin, principal=principal, arguments={}, grant=Grant(twin, principal))


@pytest.mark.django_db(transaction=True)
@_CORES
def test_a_missing_row_under_output_hooks_is_still_not_found(dispatch: Dispatch) -> None:
    """The view's output layers ride on a result's context, and a missing row has none."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_row)

    result = dispatch(
        spec,
        params={"pk": 999},
        view_hooks=ViewHooks(output_serializer_context=lambda value: {"layer": 2}),
    )

    assert result.kind == "not_found"
