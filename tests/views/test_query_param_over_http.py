"""A declared ``QueryParam`` on a live view is its query string's, never the body's.

Off HTTP, a transport's offline request carries no query string of the caller's,
so the value a caller sends for a declared ``QueryParam`` is put onto
``request.query_params``, where a serializer or a permission reading it finds it.
A live DRF view's query string is the client's URL, which its permission classes
have already read by the time the call is dispatched: a body value written there,
or handed to the service as the parameter, would reach ``has_object_permission``
and the service as though the URL had carried it, after ``has_permission`` was
asked without it.
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
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.offline_service_view import OfflineServiceView
from tests.testapp.models import Post

_factory = APIRequestFactory()
SEEN: dict[str, Any] = {}


class _NotAllUnlessQueried(BasePermission):
    """Refuses ``?scope=all``: everyone else is scoped to what they own."""

    def has_permission(self, request: Any, view: Any) -> bool:
        SEEN["permission"] = request.query_params.get("scope")
        return request.query_params.get("scope") != "all"

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        SEEN["object_permission"] = request.query_params.get("scope")
        return True


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post, request: Any, scope: str = "unset") -> None:
    SEEN["query"] = request.query_params.get("scope")
    SEEN["scope"] = scope


_SPEC = ServiceSpec(
    service=_touch,
    reads=(QueryParam("scope"),),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    atomic=False,
)


class _View(ServiceUpdateView):
    queryset = Post.objects.all()
    permission_classes = [_NotAllUnlessQueried]
    spec = _SPEC


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


@pytest.mark.django_db
def test_a_body_value_never_reaches_a_live_requests_query_string() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(_factory.put("/", {"scope": "all"}, format="json"), pk=post.pk)

    assert response.status_code == 204, response.data
    assert SEEN == {
        "permission": None,
        "object_permission": None,
        "query": None,
        "scope": "unset",
    }


@pytest.mark.django_db
def test_the_query_string_is_still_refused_where_the_permission_refuses_it() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(_factory.put("/?scope=all", {}, format="json"), pk=post.pk)

    assert response.status_code == 403
    assert "query" not in SEEN


@pytest.mark.django_db
def test_the_query_string_is_the_parameter_on_a_live_view() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(
        _factory.put("/?scope=mine", {"scope": "all"}, format="json"), pk=post.pk
    )

    assert response.status_code == 204, response.data
    assert SEEN == {
        "permission": "mine",
        "object_permission": "mine",
        "query": "mine",
        "scope": "mine",
    }


def _live_view(path: str) -> _View:
    view = _View()
    view.request = Request(_factory.put(path))
    view.kwargs = {}
    return view


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["/", "/?scope=mine"], ids=["body-only", "both"])
async def test_a_body_value_never_reaches_a_live_requests_query_string_on_either_core(
    path: str,
) -> None:
    post = await Post.objects.acreate(title="p")
    seen: list[dict[str, Any]] = []
    for core in ("sync", "async"):
        view = _live_view(path)
        kwargs: dict[str, Any] = {
            "user": None,
            "params": {"pk": post.pk, "scope": "all"},
            "request": view.request,
            "view": view,
        }
        SEEN.clear()
        if core == "sync":
            await sync_to_async(dispatch_spec, thread_sensitive=True)(_SPEC, **kwargs)
        else:
            await adispatch_spec(_SPEC, **kwargs)
        seen.append({**SEEN, "url": view.request.query_params.get("scope")})

    expected = "mine" if "scope" in path else None
    assert (
        seen[0]
        == seen[1]
        == {
            "query": expected,
            "scope": expected or "unset",
            "url": expected,
        }
    )


@pytest.mark.django_db
def test_off_http_a_body_value_goes_onto_the_offline_request() -> None:
    post = Post.objects.create(title="p")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})

    dispatch_spec(_SPEC, user=None, params={"scope": "all"}, request=view.request, view=view)

    assert SEEN == {"query": "all", "scope": "all"}
    assert view.request.query_params.get("scope") == "all"
