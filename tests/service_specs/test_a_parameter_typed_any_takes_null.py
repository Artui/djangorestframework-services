"""A parameter typed ``Any``, or not typed at all, takes ``None``.

``Any`` admits every value, ``None`` included, so neither the server's ``None``
nor a client's ``null`` is refused for it, on a selector or a service, off
HTTP or over a live view.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from asgiref.sync import async_to_sync
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Post


def _typed(scope: Any = None) -> list[Any]:
    return [scope]


def _untyped(scope=None):  # noqa: ANN001, ANN202 - the case under test
    return [scope]


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_SELECTORS = pytest.mark.parametrize("selector", [_typed, _untyped], ids=["any", "untyped"])


@pytest.mark.django_db
@_CORES
@_SELECTORS
def test_a_selector_takes_the_provider_s_none(
    dispatch: Callable[..., DispatchResult], selector: Callable[..., Any]
) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        kwargs=lambda: {"scope": None},
        permission_classes=[],
    )

    assert dispatch(spec, params={}).value == [None]


@pytest.mark.django_db
@_CORES
@_SELECTORS
def test_a_selector_takes_the_client_s_null(
    dispatch: Callable[..., DispatchResult], selector: Callable[..., Any]
) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector, permission_classes=[])

    assert dispatch(spec, params={"scope": None}).value == [None]


class _Out(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["title"]


@pytest.mark.django_db
def test_over_a_live_view_a_route_capture_the_route_lacks_reads_as_none() -> None:
    seen: list[Any] = []

    def posts(scope: Any = None) -> Any:
        seen.append(scope)
        return Post.objects.all()

    view = type(
        "_View",
        (SelectorListView,),
        {
            "spec": SelectorSpec(
                kind=SelectorKind.LIST,
                selector=posts,
                output_serializer=_Out,
                kwargs=lambda view: {"scope": view.kwargs.get("project_pk")},
            ),
            "permission_classes": [],
            "authentication_classes": [],
        },
    ).as_view()

    response = view(APIRequestFactory().get("/"))

    assert response.status_code == 200, response.data
    assert seen == [None]


def _service(*, scope=None):  # noqa: ANN001, ANN202 - the case under test
    return {"scope": scope}


@pytest.mark.django_db
@_CORES
def test_a_service_takes_the_provider_s_none(dispatch: Callable[..., DispatchResult]) -> None:
    spec = ServiceSpec(
        service=_service, kwargs=lambda: {"scope": None}, permission_classes=[], atomic=False
    )

    assert dispatch(spec, params={}).value == {"scope": None}


@pytest.mark.django_db
@_CORES
def test_a_service_spread_its_arguments_takes_the_client_s_null(
    dispatch: Callable[..., DispatchResult],
) -> None:
    spec = ServiceSpec(service=_service, permission_classes=[], atomic=False)

    result = dispatch(
        spec, params={"scope": None}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS
    )

    assert result.value == {"scope": None}


_NO_POST = cast(Post, None)


def test_a_class_no_parameter_type_names_is_still_no_none() -> None:
    """Only ``Any`` is widened: a parameter typed by a model, say, is read as ``any`` and not null."""

    def by(scope: Any = None, owner: Post | None = None, post: Post = _NO_POST) -> Any:
        return [scope, owner, post]

    reads = {
        parameter.name: parameter.nullable
        for parameter in kernel_spec(
            SelectorSpec(kind=SelectorKind.LIST, selector=by, permission_classes=[])
        ).reads
    }

    assert reads == {"scope": True, "owner": True, "post": False}
