"""Over HTTP, dropping a ``NotClientInput`` key matters only in a target lookup.

A selector view dispatches with ``ArgumentBinding.BUNDLE`` and a mutation's service
with its bundled ``data``, so neither spreads the request into a keyword pool. A
service's ``instance_selector_spec`` and ``collection_selector_spec`` are
different: the core resolves them with the request body as their argument
channel. So the body's value for a key the lookup marks ``NotClientInput`` is
dropped there too, while a route capture of that name still fills it.

``view`` is a seed, as ``request`` is: every callable declaring it is handed the
calling view, and no body, query string or route capture of that name stands in
for it. The spec's provider returning it is refused, as a provider's key for any
seed is; a view hook's value for it is dropped, as a hook's value for any seed is.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
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


def _post_for_required_team(*, pk: int, team: Annotated[str, NotClientInput]) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk, title=team)


class _RequiredTeamView(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_touch,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_post_for_required_team
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.mark.django_db
def test_the_bodys_value_for_a_required_hidden_lookup_key_leaves_the_type_error() -> None:
    """With no default, dropping the body's value is not silent: the lookup raises its
    own ``TypeError``, which DRF does not handle, so Django answers ``500``. It is
    not refused as a missing argument, because the body cannot fill it."""
    post = Post.objects.create(title="alpha")

    with pytest.raises(TypeError, match="team"):
        _RequiredTeamView.as_view()(
            factory.patch("/", {"team": "alpha"}, format="json"), pk=post.pk
        )


@pytest.mark.django_db
def test_a_route_capture_fills_a_required_hidden_lookup_key() -> None:
    post = Post.objects.create(title="alpha")

    response = _RequiredTeamView.as_view()(
        factory.patch("/", {"team": "other-team"}, format="json"), pk=post.pk, team="alpha"
    )

    assert response.status_code == 200


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
def test_a_lookup_declaring_view_is_handed_the_calling_view(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _ViewLookupView.as_view()(
        factory.put("/", {"view": "spoofed"}, format="json"), pk=post.pk, view="route"
    )

    assert response.status_code == 200
    assert len(seen) == 1
    assert isinstance(seen[0], _ViewLookupView)


class _SeedView(SelectorRetrieveView):
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_post_seeing_view, output_serializer=PostSerializer
    )


@pytest.mark.django_db
def test_a_selector_declaring_view_is_handed_the_calling_view(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _SeedView.as_view()(factory.get("/", {"view": "spoofed"}), pk=post.pk)

    assert response.status_code == 200
    assert len(seen) == 1
    assert isinstance(seen[0], _SeedView)


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
def test_handing_the_selector_the_view_through_the_spec_is_refused(seen: list[Any]) -> None:
    """The view is already the selector's, as a seed, and the spec's own
    provider never outranks a seed: so the hand-over is refused rather than
    silently ignored."""
    post = Post.objects.create(title="p")

    with pytest.raises(ImproperlyConfigured, match="seeds itself"):
        _ProviderView.as_view()(factory.get("/"), pk=post.pk)
    assert seen == []


@pytest.mark.django_db
def test_a_view_hook_handing_over_the_view_is_dropped_for_the_seed(seen: list[Any]) -> None:
    """Behaviour change: a view hook's value naming a seed is dropped and the seed
    wins, never overlaid and never refused. The selector still sees this view."""
    post = Post.objects.create(title="p")

    response = _HookView.as_view()(factory.get("/"), pk=post.pk)

    assert response.status_code == 200
    assert len(seen) == 1
    assert isinstance(seen[0], _HookView)


def _post_for_selector_team(
    *, pk: int, team: Annotated[str, NotClientInput] = "own-team"
) -> QuerySet[Post]:
    _SEEN.append(team)
    return Post.objects.filter(pk=pk)


class _TeamRetrieveView(SelectorRetrieveView):
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_post_for_selector_team,
        output_serializer=PostSerializer,
    )


@pytest.mark.django_db
def test_a_route_capture_fills_a_selectors_hidden_key(seen: list[Any]) -> None:
    """A selector spec's own selector: the capture reaches it as a call extra."""
    post = Post.objects.create(title="p")

    response = _TeamRetrieveView.as_view()(
        factory.get("/", {"team": "query-team"}), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200
    assert seen == ["route-team"]


@pytest.mark.django_db
def test_without_a_route_capture_a_selectors_hidden_key_keeps_its_default(
    seen: list[Any],
) -> None:
    post = Post.objects.create(title="p")

    response = _TeamRetrieveView.as_view()(factory.get("/", {"team": "query-team"}), pk=post.pk)

    assert response.status_code == 200
    assert seen == ["own-team"]


class _HookTeamRetrieveView(_TeamRetrieveView):
    def get_selector_kwargs(self) -> dict[str, Any]:
        return {"team": "hook-team"}


@pytest.mark.django_db
def test_a_view_hooks_value_outranks_the_route_capture(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _HookTeamRetrieveView.as_view()(factory.get("/"), pk=post.pk, team="route-team")

    assert response.status_code == 200
    assert seen == ["hook-team"]
