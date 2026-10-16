"""A declared ``UrlKwarg`` on a live view is its route's, never the body's.

Off HTTP, a transport's offline view carries no route, so the value a caller
sends for a declared ``UrlKwarg`` is put onto ``view.kwargs``, where a provider
reading the route finds it. A live DRF view's ``kwargs`` are its URLconf's, which
permission classes read as the author's: a body value written there, or handed to
the service as the argument, would reach ``has_object_permission`` and the service
as though the route had captured it, after ``has_permission`` was asked without it.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.db.models import QuerySet
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
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
SEEN: dict[str, Any] = {}


class _RecordsTheRoute(BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        SEEN["route"] = dict(view.kwargs)
        return True


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _post_by_pk_in_org(*, pk: int, org: str = "none") -> QuerySet[Post]:
    SEEN["lookup_org"] = org
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post, org: str = "none") -> Post:
    SEEN["org"] = org
    return instance


_SPEC = ServiceSpec(
    service=_touch,
    reads=(UrlKwarg("org"),),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer),
    atomic=False,
)


class _View(ServiceUpdateView):
    queryset = Post.objects.all()
    permission_classes = [_RecordsTheRoute]
    spec = _SPEC


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


@pytest.mark.django_db
def test_a_body_value_never_reaches_a_live_views_kwargs() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(_factory.put("/", {"org": "acme"}, format="json"), pk=post.pk)

    assert response.status_code == 200, response.data
    assert SEEN["route"] == {"pk": post.pk}
    assert SEEN["org"] == "none"


def _lookup_view(*reads: UrlKwarg) -> type[ServiceUpdateView]:
    """A view whose target lookup, not its service, takes ``org``."""

    class Lookup(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(
            service=_touch,
            reads=reads,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_by_pk_in_org
            ),
            output_selector_spec=_SPEC.output_selector_spec,
            atomic=False,
        )

    return Lookup


@pytest.mark.django_db
def test_a_body_value_never_reaches_a_live_views_lookup_parameter_it_declares() -> None:
    post = Post.objects.create(title="p")

    response = _lookup_view(UrlKwarg("org")).as_view()(
        _factory.put("/", {"org": "acme"}, format="json"), pk=post.pk
    )

    assert response.status_code == 200, response.data
    assert SEEN["lookup_org"] == "none"


@pytest.mark.django_db
def test_a_name_no_reads_declares_is_still_the_clients_to_the_lookup() -> None:
    """Only a declared ``UrlKwarg`` is dropped: a lookup's own parameter is an ordinary read."""
    post = Post.objects.create(title="p")

    response = _lookup_view().as_view()(
        _factory.put("/", {"org": "acme"}, format="json"), pk=post.pk
    )

    assert response.status_code == 200, response.data
    assert SEEN["lookup_org"] == "acme"


@pytest.mark.django_db
def test_the_route_is_the_argument_on_a_live_view() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(
        _factory.put("/", {"org": "evil"}, format="json"), pk=post.pk, org="acme"
    )

    assert response.status_code == 200, response.data
    assert SEEN["route"] == {"pk": post.pk, "org": "acme"}
    assert SEEN["org"] == "acme"


def _live_view(**kwargs: Any) -> _View:
    view = _View()
    view.request = Request(_factory.put("/"))
    view.kwargs = kwargs
    return view


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", [{}, {"org": "acme"}], ids=["no-capture", "captured"])
async def test_a_body_value_never_reaches_a_live_views_argument_on_either_core(
    route: dict[str, Any],
) -> None:
    post = await Post.objects.acreate(title="p")
    seen: list[dict[str, Any]] = []
    for core in ("sync", "async"):
        view = _live_view(**route)
        kwargs: dict[str, Any] = {
            "user": None,
            "params": {"pk": post.pk, "org": "evil"},
            "request": view.request,
            "view": view,
        }
        SEEN.clear()
        if core == "sync":
            await sync_to_async(dispatch_spec, thread_sensitive=True)(_SPEC, **kwargs)
        else:
            await adispatch_spec(_SPEC, **kwargs)
        seen.append({**SEEN, "kwargs": dict(view.kwargs)})

    expected = route.get("org", "none")
    assert seen[0] == seen[1] == {"org": expected, "kwargs": route}


@pytest.mark.django_db
def test_off_http_a_body_value_goes_onto_the_offline_views_kwargs() -> None:
    post = Post.objects.create(title="p")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})

    dispatch_spec(_SPEC, user=None, params={"org": "acme"}, view=view)

    assert view.kwargs == {"pk": post.pk, "org": "acme"}
    assert SEEN["org"] == "acme"


def _posts_in_org(*, org: str = "none") -> QuerySet[Post]:
    SEEN["selector_org"] = org
    return Post.objects.all()


@pytest.mark.django_db
def test_a_query_value_never_reaches_a_live_selectors_declared_url_kwarg() -> None:
    class Listing(SelectorListView):
        queryset = Post.objects.all()
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts_in_org,
            output_serializer=PostSerializer,
            reads=(UrlKwarg("org"),),
        )

    response = Listing.as_view()(_factory.get("/?org=acme"))
    assert response.status_code == 200, response.data
    assert SEEN["selector_org"] == "none"
    SEEN.clear()

    response = Listing.as_view()(_factory.get("/?org=acme"), org="route")
    assert response.status_code == 200, response.data
    assert SEEN["selector_org"] == "route"
