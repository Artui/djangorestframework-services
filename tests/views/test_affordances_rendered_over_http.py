"""Each HTTP render site serves the same affordance answers ``render_spec_output`` does.

DRF's generic views serialize for themselves, so every site that renders a
selector's rows has to add the answers on its own. One test per site, read off
the rendered response, plus the parity check that a browser and an agent
transport are served the same object.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.db.models import Q, QuerySet
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    Affordance,
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    SelectorViewSet,
    ServiceSpec,
    ServiceUpdateView,
    dispatch_spec,
    render_spec_output,
    selector_action,
)
from tests.testapp.models import Post

factory = APIRequestFactory()

UNPUBLISHED = Affordance(
    code="already_published", reason="Went out at 09:00.", when=Q(published=False)
)
PUBLISH = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED])

_AVAILABLE = {"publish": {"available": True}}
_REFUSED = {
    "publish": {"available": False, "code": "already_published", "reason": "Went out at 09:00."}
}


class _PostOut(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()


class _OnePerPage(PageNumberPagination):
    page_size = 1


def _posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _post(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _listing(**fields: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_posts,
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
        **fields,
    )


def _detail(**fields: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_post,
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
        **fields,
    )


def _body(response: Any) -> Any:
    response.render()
    return json.loads(response.content)


@pytest.fixture
def posts(db: Any) -> tuple[Post, Post]:
    return Post.objects.create(title="draft"), Post.objects.create(title="out", published=True)


def _answers(body: list[dict[str, Any]]) -> list[Any]:
    return [row["affordances"] for row in body]


# --- standalone views -------------------------------------------------------------------


def test_the_list_view(posts: tuple[Post, Post]) -> None:
    class _View(SelectorListView):
        spec = _listing()

    assert _answers(_body(_View.as_view()(factory.get("/")))) == [_AVAILABLE, _REFUSED]


def test_the_list_view_paginated(posts: tuple[Post, Post]) -> None:
    class _View(SelectorListView):
        spec = _listing()
        pagination_class = _OnePerPage

    body = _body(_View.as_view()(factory.get("/", {"page": 2})))
    assert body["count"] == 2
    assert _answers(body["results"]) == [_REFUSED]


def test_the_list_view_serves_what_an_agent_transport_renders(posts: tuple[Post, Post]) -> None:
    class _View(SelectorListView):
        spec = _listing()

    rows = list(dispatch_spec(_View.spec, user=None, params={}).value)
    rendered = json.loads(json.dumps(render_spec_output(_View.spec, rows, many=True)))
    assert _body(_View.as_view()(factory.get("/"))) == rendered


def test_the_list_view_keeps_a_subclass_list_override(posts: tuple[Post, Post]) -> None:
    """Declaring affordances must not route a request around the view's own ``list``.

    The viewset mixin adds the answers inside ``list``; the standalone view has to
    as well, or a subclass extending ``list`` stops running the moment its spec
    declares one.
    """

    class _View(SelectorListView):
        spec = _listing()

        def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
            response = super().list(request, *args, **kwargs)
            response["X-Extended"] = "yes"
            return response

    response = _View.as_view()(factory.get("/"))
    assert response["X-Extended"] == "yes"
    assert _answers(_body(response)) == [_AVAILABLE, _REFUSED]


def test_the_list_view_over_a_selector_returning_a_list(posts: tuple[Post, Post]) -> None:
    class _View(SelectorListView):
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: list(_posts()),
            output_serializer=_PostOut,
            affordances={"publish": PUBLISH},
        )

    assert _answers(_body(_View.as_view()(factory.get("/")))) == [_AVAILABLE, _REFUSED]


def test_the_retrieve_view_over_a_selector_returning_an_instance(
    posts: tuple[Post, Post],
) -> None:
    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: Post.objects.get(pk=pk),
            output_serializer=_PostOut,
            affordances={"publish": PUBLISH},
        )

    assert _body(_View.as_view()(factory.get("/"), pk=posts[1].pk))["affordances"] == _REFUSED


def test_the_list_view_declaring_nothing_serves_drfs_own_response(
    posts: tuple[Post, Post],
) -> None:
    class _View(SelectorListView):
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, output_serializer=_PostOut)

    assert _body(_View.as_view()(factory.get("/"))) == [
        {"id": posts[0].pk, "title": "draft"},
        {"id": posts[1].pk, "title": "out"},
    ]


def test_the_retrieve_view(posts: tuple[Post, Post]) -> None:
    class _View(SelectorRetrieveView):
        spec = _detail()

    body = _body(_View.as_view()(factory.get("/"), pk=posts[1].pk))
    assert body == {"id": posts[1].pk, "title": "out", "affordances": _REFUSED}


def test_the_retrieve_view_missing_row_is_still_a_404(posts: tuple[Post, Post]) -> None:
    class _View(SelectorRetrieveView):
        spec = _detail()

    assert _View.as_view()(factory.get("/"), pk=10_000).status_code == 404


def test_the_retrieve_view_nullable_none_is_still_null(db: Any) -> None:
    class _View(SelectorRetrieveView):
        spec = _detail(allow_none=True)

    response = _View.as_view()(factory.get("/"), pk=10_000)
    assert (response.status_code, response.data) == (200, None)


# --- viewsets --------------------------------------------------------------------------


class _Posts(SelectorViewSet):
    action_specs = {"list": _listing(), "retrieve": _detail()}


def test_the_viewset_list(posts: tuple[Post, Post]) -> None:
    view = _Posts.as_view({"get": "list"})
    assert _answers(_body(view(factory.get("/")))) == [_AVAILABLE, _REFUSED]


def test_the_viewset_retrieve(posts: tuple[Post, Post]) -> None:
    view = _Posts.as_view({"get": "retrieve"})
    assert _body(view(factory.get("/"), pk=posts[0].pk))["affordances"] == _AVAILABLE


def test_the_viewset_retrieve_nullable_none_is_still_null(db: Any) -> None:
    class _Nullable(SelectorViewSet):
        action_specs = {"retrieve": _detail(allow_none=True)}

    view = _Nullable.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=10_000)
    assert (response.status_code, response.data) == (200, None)


class _Actions(GenericViewSet):
    queryset = Post.objects.all()

    @selector_action(_detail())
    def one(self, request: Any) -> None: ...

    @selector_action(_listing())
    def many(self, request: Any) -> None: ...


class _PaginatedActions(_Actions):
    pagination_class = _OnePerPage


def test_a_selector_action_detail(posts: tuple[Post, Post]) -> None:
    view = _Actions.as_view({"get": "one"})
    assert _body(view(factory.get("/"), pk=posts[1].pk))["affordances"] == _REFUSED


def test_a_selector_action_list(posts: tuple[Post, Post]) -> None:
    view = _Actions.as_view({"get": "many"})
    assert _answers(_body(view(factory.get("/")))) == [_AVAILABLE, _REFUSED]


def test_a_selector_action_list_paginated(posts: tuple[Post, Post]) -> None:
    view = _PaginatedActions.as_view({"get": "many"})
    assert _answers(_body(view(factory.get("/")))["results"]) == [_AVAILABLE]


# --- a mutation's response ---------------------------------------------------------------


def _publish(*, instance: Post) -> Post:
    instance.published = True
    instance.save(update_fields=["published"])
    return instance


def test_an_update_response_carries_the_answers_for_the_row_it_wrote(
    posts: tuple[Post, Post],
) -> None:
    """Published by this very call, so the answer in the response is already the new
    one: the re-fetch runs after the write."""

    class _View(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(
            service=_publish,
            affordances=[UNPUBLISHED],
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, result: Post.objects.filter(pk=result.pk),
                output_serializer=_PostOut,
                affordances={"publish": PUBLISH},
            ),
        )

    response = _View.as_view()(factory.patch("/", {}, format="json"), pk=posts[0].pk)
    assert response.status_code == 200
    assert _body(response)["affordances"] == _REFUSED


def test_an_update_whose_refetch_finds_nothing_carries_no_answers(
    posts: tuple[Post, Post],
) -> None:
    """``None`` is not a row. The response renders exactly as it did before the spec
    declared anything, rather than failing to find annotations on nothing."""

    def _view(affordances: Any) -> Any:
        class _View(ServiceUpdateView):
            queryset = Post.objects.all()
            spec = ServiceSpec(
                service=_publish,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda **_: Post.objects.none(),
                    output_serializer=_PostOut,
                    affordances=affordances,
                ),
            )

        return _View.as_view()

    request = factory.patch("/", {}, format="json")
    declared = _view({"publish": PUBLISH})(request, pk=posts[0].pk)
    undeclared = _view(None)(factory.patch("/", {}, format="json"), pk=posts[1].pk)

    assert declared.status_code == undeclared.status_code
    assert declared.data == undeclared.data
