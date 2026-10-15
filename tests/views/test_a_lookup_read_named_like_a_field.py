"""A target lookup reading a name that is also a writable input field.

Renaming a row by the key that finds it - a lookup by ``slug`` and a body
``slug`` - is this shape: the route's capture scopes the lookup, and the body's
value is the serializer's field, as on 0.57.0. The two never meet, so the spec
dispatches rather than being refused for declaring the name twice. A client's
value is never the lookup's: with no route capture the lookup keeps its own
default, and a read with none is refused as missing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.offline_service_view import OfflineServiceView
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _form(values: dict[str, Any]) -> QueryDict:
    body = QueryDict(mutable=True)
    for name, value in values.items():
        body[name] = value
    return body


_BODY = pytest.mark.parametrize("body", [dict, _form], ids=["mapping", "form"])


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _spec(seen: dict[str, Any], *, default: bool = False) -> ServiceSpec[Any, Any, Any]:
    if default:

        def selector(*, pk: int, title: str = "lookup-default") -> Any:
            seen["lookup_title"] = title
            return Post.objects.filter(pk=pk)

    else:

        def selector(*, pk: int, title: str) -> Any:
            seen["lookup_title"] = title
            return Post.objects.filter(pk=pk, title=title)

    def service(*, instance: Any, data: Any, title: str = "svc-default") -> Any:
        seen.update(title=title, data=dict(data))
        return instance

    return ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


def _view(spec: ServiceSpec[Any, Any, Any], **attrs: Any) -> type[ServiceUpdateView]:
    return type(
        "_View",
        (ServiceUpdateView,),
        {"queryset": Post.objects.all(), "permission_classes": [], "spec": spec, **attrs},
    )


# The service's ``title`` is the field's validated value, as any validated value
# reaches the parameter of its name.
_ROUTED = {"lookup_title": "old", "title": "new", "data": {"title": "new"}}


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_the_route_scopes_the_lookup_and_the_body_fills_the_field_over_http(fmt: str) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="old")

    response = _view(_spec(seen)).as_view()(
        _factory.put("/", {"title": "new"}, format=fmt), pk=post.pk, title="old"
    )

    assert response.status_code == 200, response.data
    assert seen == _ROUTED


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_the_route_scopes_the_lookup_and_the_body_fills_the_field_on_a_live_async_view(
    fmt: str,
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="old")
    view = ServiceUpdateView()
    view.request = Request(
        _factory.put("/", {"title": "new"}, format=fmt),
        parsers=[JSONParser(), MultiPartParser()],
    )
    view.request.user = AnonymousUser()
    view.kwargs = {"pk": post.pk, "title": "old"}
    view.format_kwarg = None

    async_to_sync(adispatch_spec)(
        _spec(seen), user=None, params=view.request.data, request=view.request, view=view
    )

    assert seen == _ROUTED


@pytest.mark.django_db
@_CORES
@_BODY
def test_the_route_scopes_the_lookup_and_the_body_fills_the_field_off_http(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="old")
    view = OfflineServiceView(
        request=Request(_factory.put("/")), kwargs={"pk": post.pk, "title": "old"}
    )

    dispatch(_spec(seen), params=body({"title": "new"}), view=view)

    assert seen == _ROUTED


@pytest.mark.django_db
@_CORES
@_BODY
def test_with_no_route_capture_the_clients_value_never_scopes_the_lookup(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    """The client's ``title`` is the field's alone, so the lookup keeps its default."""
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="old")

    dispatch(_spec(seen, default=True), params=body({"pk": post.pk, "title": "new"}))

    assert seen == {"lookup_title": "lookup-default", "title": "new", "data": {"title": "new"}}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("author", ["hook", "provider"])
def test_an_authors_value_for_the_read_scopes_the_lookup_and_fills_the_field(
    dispatch: Dispatch, author: str
) -> None:
    """An author's value is the server's: the lookup reads it, and the field validates it."""
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="server")
    view = OfflineServiceView(
        request=Request(_factory.put("/")), kwargs={"pk": post.pk, "title": "old"}
    )
    spec = _spec(seen)
    hooks = ViewHooks()
    if author == "hook":
        hooks = ViewHooks(extra_kwargs={"title": "server"})
    else:
        spec = dataclasses.replace(spec, kwargs=lambda: {"title": "server"})

    dispatch(spec, params={"title": "new"}, view=view, view_hooks=hooks)

    assert seen == {"lookup_title": "server", "title": "server", "data": {"title": "server"}}


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_with_no_route_capture_a_lookup_read_with_no_default_is_refused_as_missing_over_http(
    fmt: str,
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="new")

    response = _view(_spec(seen)).as_view()(
        _factory.put("/", {"title": "new"}, format=fmt), pk=post.pk
    )

    assert response.status_code == 400
    assert response.data == {"title": ["This field is required."]}
    assert seen == {}


@pytest.mark.django_db
@_CORES
@_BODY
def test_with_no_route_capture_a_lookup_read_with_no_default_is_refused_as_missing(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="new")

    with pytest.raises(ValidationError) as excinfo:
        dispatch(_spec(seen), params=body({"pk": post.pk, "title": "new"}))

    assert excinfo.value.get_codes() == {"title": ["required"]}
    assert seen == {}
