"""Over HTTP, dropping a ``NotClientInput`` key matters only in a target lookup.

A selector view dispatches with ``ArgumentBinding.BUNDLE`` and a mutation's service
with its bundled ``data``, so neither spreads the request into a keyword pool. A
service's ``instance_selector_spec`` and ``collection_selector_spec`` are
different: the core resolves them with the request body as their argument
channel. So the body's value for a key the lookup marks ``NotClientInput`` is
dropped there too, while a route capture of that name still fills it.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.db.models import QuerySet
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
)
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

factory = APIRequestFactory()
_SEEN: list[Any] = []


def _post_for_team(*, pk: int, team: Annotated[str, NotClientInput] = "own-team") -> QuerySet[Post]:
    _SEEN.append(team)
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post) -> Post:
    return instance


class _View(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_touch,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_for_team),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.fixture
def seen() -> list[Any]:
    _SEEN.clear()
    return _SEEN


@pytest.mark.django_db
def test_the_bodys_value_for_a_hidden_lookup_key_is_dropped(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(factory.put("/", {"team": "other-team"}, format="json"), pk=post.pk)

    assert response.status_code == 200
    assert seen == ["own-team"]


@pytest.mark.django_db
def test_a_route_capture_still_fills_a_hidden_lookup_key(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(
        factory.put("/", {"team": "other-team"}, format="json"), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200
    assert seen == ["route-team"]


def _posts_for_team(*, team: Annotated[str, NotClientInput] = "own-team") -> QuerySet[Post]:
    _SEEN.append(team)
    return Post.objects.order_by("id")


def _count(*, collection: QuerySet[Post]) -> dict[str, int]:
    return {"count": collection.count()}


class _BulkView(ServiceUpdateView):
    spec = ServiceSpec(
        service=_count,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_posts_for_team),
        atomic=False,
    )


@pytest.mark.django_db
def test_the_bodys_value_for_a_hidden_collection_lookup_key_is_dropped(seen: list[Any]) -> None:
    Post.objects.create(title="p")

    response = _BulkView.as_view()(factory.put("/", {"team": "other-team"}, format="json"))

    assert response.status_code == 200
    assert response.data == {"count": 1}
    assert seen == ["own-team"]


@pytest.mark.django_db
def test_a_route_capture_still_fills_a_hidden_collection_lookup_key(seen: list[Any]) -> None:
    response = _BulkView.as_view()(
        factory.put("/", {"team": "other-team"}, format="json"), team="route-team"
    )

    assert response.status_code == 200
    assert seen == ["route-team"]
