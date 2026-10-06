"""Over HTTP, dropping a ``NotClientInput`` key matters only in a target lookup.

A selector view dispatches with ``ArgumentBinding.BUNDLE`` and a mutation's service
with its bundled ``data``, so neither spreads the request into a keyword pool. A
service's ``instance_selector_spec`` and ``collection_selector_spec`` are
different: the core resolves them with the request body as their argument
channel. So the body's value for a key the lookup marks ``NotClientInput`` is
dropped there too, while a route capture of that name still fills it.

``view`` is treated the same way, as though every callable marked it: no pool
carries it, so over HTTP a selector receives it only from a hook or provider, and
a lookup only from those or a route capture. Those still deliver the real view;
only a request body's ``view`` stops reaching a lookup.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.db.models import QuerySet
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    NotClientInput,
    SelectorKind,
    SelectorRetrieveView,
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


# --- ``view`` ---------------------------------------------------------------------


def _post_seeing_view(*, pk: int, view: Any = None) -> QuerySet[Post]:
    _SEEN.append(view)
    return Post.objects.filter(pk=pk)


class _ViewLookupView(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_touch,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_seeing_view),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.mark.django_db
def test_the_bodys_view_is_dropped_before_a_lookup(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _ViewLookupView.as_view()(
        factory.put("/", {"view": "spoofed"}, format="json"), pk=post.pk
    )

    assert response.status_code == 200
    assert seen == [None]


@pytest.mark.django_db
def test_a_route_capture_named_view_still_fills_a_lookup(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _ViewLookupView.as_view()(
        factory.put("/", {"view": "spoofed"}, format="json"), pk=post.pk, view="route"
    )

    assert response.status_code == 200
    assert seen == ["route"]


class _HookView(SelectorRetrieveView):
    """Hands itself to the selector through its kwargs hook."""

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_post_seeing_view, output_serializer=PostSerializer
    )

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {"view": self}


def _hand_over_the_view(view: Any) -> dict[str, Any]:
    return {"view": view}


class _ProviderView(SelectorRetrieveView):
    """Hands itself to the selector through the spec's ``kwargs=`` provider."""

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_post_seeing_view,
        output_serializer=PostSerializer,
        kwargs=_hand_over_the_view,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("view_cls", [_HookView, _ProviderView], ids=["hook", "provider"])
def test_a_selector_view_still_hands_the_selector_the_real_view(
    seen: list[Any], view_cls: type[SelectorRetrieveView]
) -> None:
    """A query string's ``view`` never reached it either: a selector view binds
    ``BUNDLE`` and spreads nothing."""
    post = Post.objects.create(title="p")

    response = view_cls.as_view()(factory.get("/", {"view": "spoofed"}), pk=post.pk)

    assert response.status_code == 200
    assert len(seen) == 1
    assert isinstance(seen[0], view_cls)
