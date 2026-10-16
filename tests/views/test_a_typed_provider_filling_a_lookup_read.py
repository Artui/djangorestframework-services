"""A typed ``kwargs`` provider annotated to fill a name the target lookup reads is refused.

The spec's ``kwargs`` provider's values reach the service once the target is
resolved, so a key its return annotation fills never reaches the lookup, and
the kernel refuses the spec rather than demand that key of the caller. 0.57.0
dispatched it. An untyped provider names no keys, so its ``pk`` still picks the
row, as does a view hook's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.registry.spec_registry import SpecRegistry
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

_factory = APIRequestFactory()
_REFUSED = (
    r"ServiceSpec\.kwargs is annotated to fill \['pk'\], which "
    r"ServiceSpec\.instance_selector_spec\.reads declares"
)


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


class _FillsPk(TypedDict):
    pk: int


def _spec(provider: Callable[[], Any]) -> ServiceSpec[Any, Any, Any]:
    def selector(*, pk: int) -> Any:
        return Post.objects.filter(pk=pk)

    def service(*, instance: Any, data: Any) -> Any:
        return instance

    return ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        kwargs=provider,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        permission_classes=[],
        atomic=False,
    )


def _typed(pk: int) -> Callable[[], _FillsPk]:
    def provider() -> _FillsPk:
        return {"pk": pk}

    return provider


def _untyped(pk: int) -> Callable[[], Any]:
    def provider():  # unannotated on purpose: it names no keys
        return {"pk": pk}

    return provider


@pytest.mark.django_db
@pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
def test_dispatch_refuses_the_spec(dispatch: Callable[..., DispatchResult]) -> None:
    post = Post.objects.create(title="old")

    with pytest.raises(ImproperlyConfigured, match=_REFUSED):
        dispatch(_spec(_typed(post.pk)), params={"title": "new"})


@pytest.mark.django_db
def test_a_live_view_refuses_the_spec() -> None:
    post = Post.objects.create(title="old")
    view = type(
        "_View",
        (ServiceUpdateView,),
        {"queryset": Post.objects.all(), "permission_classes": [], "spec": _spec(_typed(post.pk))},
    )

    with pytest.raises(ImproperlyConfigured, match=_REFUSED):
        view.as_view()(_factory.patch("/", {"title": "new"}, format="json"), pk=post.pk)


def test_registration_refuses_the_spec() -> None:
    with pytest.raises(ImproperlyConfigured, match=_REFUSED):
        SpecRegistry().register("rename", _spec(_typed(1)))


@pytest.mark.django_db
def test_an_untyped_providers_pk_picks_the_row() -> None:
    post = Post.objects.create(title="old")
    other = Post.objects.create(title="other")

    result = dispatch_spec(
        _spec(_untyped(other.pk)), user=None, params={"pk": post.pk, "title": "new"}
    )

    assert result.value == other
    assert SpecRegistry().register("rename", _spec(_untyped(1))) is None
