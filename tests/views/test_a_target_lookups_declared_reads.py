"""A target lookup's declared ``UrlKwarg`` and ``QueryParam`` are read as the spec's own are.

On a live DRF view a declared ``UrlKwarg`` is the route's alone and a declared
``QueryParam`` the query string's alone, so a body value of either name never
scopes the lookup and is never written onto ``view.kwargs`` or
``request.query_params``. Off HTTP the caller sends them as arguments, and an
``OfflineServiceView`` carries a sent value onto the view's ``kwargs`` or its
request's query string, and its query string fills a ``QueryParam`` the caller
left out. A ``reads`` entry on the target lookup is the same declaration as one
on the spec, so it is read the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    UrlKwarg,
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


class _BodyIn(serializers.Serializer):
    body = serializers.CharField(required=False)


def _spec(seen: dict[str, Any]) -> ServiceSpec[Any, Any, Any]:
    def selector(*, pk: int, q: str = "D-q", org: str = "D-org") -> Any:
        seen.update(q=q, org=org)
        return Post.objects.filter(pk=pk)

    def service(*, instance: Any, data: Any) -> Any:
        return instance

    return ServiceSpec(
        service=service,
        input_serializer=_BodyIn,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=(QueryParam("q"), UrlKwarg("org")),
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


_SENT = {"body": "x", "q": "from-body", "org": "from-body"}


def _live(url: str, fmt: str, route: dict[str, Any]) -> ServiceUpdateView:
    """A live view as DRF builds one per request, for the async core to dispatch."""
    view = ServiceUpdateView()
    view.request = Request(
        _factory.put(url, _SENT, format=fmt), parsers=[JSONParser(), MultiPartParser()]
    )
    view.request.user = AnonymousUser()
    view.kwargs = route
    view.format_kwarg = None
    return view


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
@pytest.mark.parametrize("routed", [True, False], ids=["routed", "unrouted"])
def test_a_live_view_reads_the_lookups_reads_from_its_url_alone(fmt: str, routed: bool) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    route = {"pk": post.pk, "org": "from-route"} if routed else {"pk": post.pk}
    view = type(
        "_View",
        (ServiceUpdateView,),
        {"queryset": Post.objects.all(), "permission_classes": [], "spec": _spec(seen)},
    )
    url = "/?q=from-query" if routed else "/"

    response = view.as_view()(_factory.put(url, _SENT, format=fmt), **route)

    assert response.status_code == 200, response.data
    assert seen == (
        {"q": "from-query", "org": "from-route"} if routed else {"q": "D-q", "org": "D-org"}
    )


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
@pytest.mark.parametrize("routed", [True, False], ids=["routed", "unrouted"])
def test_a_live_async_view_reads_the_lookups_reads_from_its_url_alone(
    fmt: str, routed: bool
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    route = {"pk": post.pk, "org": "from-route"} if routed else {"pk": post.pk}
    view = _live("/?q=from-query" if routed else "/", fmt, dict(route))

    async_to_sync(adispatch_spec)(
        _spec(seen), user=None, params=view.request.data, request=view.request, view=view
    )

    assert seen == (
        {"q": "from-query", "org": "from-route"} if routed else {"q": "D-q", "org": "D-org"}
    )
    # Neither the route nor the query string the permission classes read is written.
    assert view.kwargs == route
    assert "q" not in view.request.query_params or routed


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_body_value_never_reaches_a_live_views_route_or_query_string(
    dispatch: Dispatch, fmt: str
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = _live("/", fmt, {"pk": post.pk})

    dispatch(_spec(seen), params=view.request.data, request=view.request, view=view)

    assert seen == {"q": "D-q", "org": "D-org"}
    assert view.kwargs == {"pk": post.pk}
    assert dict(view.request.query_params) == {}


@pytest.mark.django_db
@_CORES
def test_an_offline_views_query_string_fills_the_lookups_query_param(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(
        request=Request(_factory.put("/?q=from-query")), kwargs={"pk": post.pk, "org": "from-route"}
    )

    dispatch(_spec(seen), params={"body": "x"}, request=view.request, view=view)

    assert seen == {"q": "from-query", "org": "from-route"}


@pytest.mark.django_db
@_CORES
@_BODY
def test_off_http_a_sent_value_reaches_the_lookup_and_the_offline_view(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})

    dispatch(
        _spec(seen),
        params=body({"body": "x", "q": "sent", "org": "sent"}),
        request=view.request,
        view=view,
    )

    assert seen == {"q": "sent", "org": "sent"}
    assert view.kwargs["org"] == "sent"
    assert view.request.query_params["q"] == "sent"


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _rename(seen: dict[str, Any], *, on: str) -> ServiceSpec[Any, Any, Any]:
    def selector(*, pk: int, title: str = "D-lookup") -> Any:
        seen["lookup"] = title
        return Post.objects.filter(pk=pk)

    def service(*, instance: Any, data: Any, title: str = "D-svc") -> Any:
        seen["service"] = title
        return instance

    reads = (UrlKwarg("title"),)
    return ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        reads=reads if on == "spec" else (),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=selector, reads=reads if on == "lookup" else ()
        ),
        atomic=False,
    )


@pytest.mark.django_db
@_CORES
@_BODY
@pytest.mark.parametrize("on", ["spec", "lookup"])
def test_a_body_value_for_a_url_kwarg_named_like_a_field_is_the_fields_alone(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any], on: str
) -> None:
    """The rename-by-key shape off HTTP with no capture: the body's ``title`` is the field's.

    It neither scopes the lookup nor goes onto the offline view's ``kwargs``, as
    with no view at all.
    """
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})

    dispatch(_rename(seen, on=on), params=body({"title": "body"}), request=view.request, view=view)

    assert seen == {"lookup": "D-lookup", "service": "body"}
    assert view.kwargs == {"pk": post.pk}
