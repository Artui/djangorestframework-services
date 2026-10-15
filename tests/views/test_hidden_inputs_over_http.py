"""Over HTTP, a ``NotClientInput`` key is the route's to fill and never the body's.

A call has one pool, and a key a callable marks ``NotClientInput`` is no argument
of it, so the body's value for one is dropped wherever it is sent: to a
service's ``instance_selector_spec`` or ``collection_selector_spec`` as much as
to a selector. The URLconf is the author's, so a route capture of that name still
fills it: a selector's, as a call extra, and a target lookup's, as a seed of the
call, unless a seed of that name, the lookup's own provider or an author's value
answers it first. An input serializer field of the same name does not stop the
capture: the field is the client's, validated from the body into ``data``.

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
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict

from rest_framework_services import (
    NotClientInput,
    QueryParam,
    SelectorKind,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    UrlKwarg,
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.types.offline_service_view import OfflineServiceView
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.unknown_arguments import UnknownArguments
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


def _post_for_team_and_org(
    *,
    pk: int,
    team: Annotated[str, NotClientInput] = "own-team",
    org: Annotated[str, NotClientInput] = "own-org",
) -> QuerySet[Post]:
    _SEEN.append((team, org))
    return Post.objects.filter(pk=pk)


class _TwoKeysView(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_touch,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_post_for_team_and_org
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.mark.django_db
def test_each_route_capture_fills_its_own_hidden_lookup_key(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    response = _TwoKeysView.as_view()(
        factory.put("/", {}, format="json"), pk=post.pk, team="route-team", org="route-org"
    )

    assert response.status_code == 200
    assert seen[0] == ("route-team", "route-org")


@pytest.mark.django_db
def test_a_registered_seed_named_like_a_hidden_lookup_key_wins_over_the_capture(
    seen: list[Any],
) -> None:
    post = Post.objects.create(title="p")
    context = build_offline_context(None, kwargs={"pk": post.pk, "team": "route-team"})

    dispatch_spec(
        _View.spec,
        user=None,
        params={},
        request=context.request,
        view=context.view,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(team=lambda: "seed-team"),
    )

    assert seen == ["seed-team"]


@pytest.mark.django_db
def test_with_no_view_a_hidden_lookup_key_keeps_its_default(seen: list[Any]) -> None:
    post = Post.objects.create(title="p")

    dispatch_spec(_View.spec, user=None, params={"pk": post.pk})

    assert seen == ["own-team"]


def _post_for_team_seen_by_both(
    *, pk: int, team: Annotated[str, NotClientInput] = "own-team"
) -> QuerySet[Post]:
    _SEEN.append(("lookup", team))
    return Post.objects.filter(pk=pk)


def _touch_seeing_team(
    *, instance: Post, team: Annotated[str, NotClientInput] = "service-default"
) -> Post:
    _SEEN.append(("service", team))
    return instance


def _both_view(*, kwargs: Any = None, lookup_kwargs: Any = None, **attrs: Any) -> Any:
    namespace: dict[str, Any] = {
        "queryset": Post.objects.all(),
        "spec": ServiceSpec(
            service=_touch_seeing_team,
            kwargs=kwargs,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_post_for_team_seen_by_both,
                kwargs=lookup_kwargs,
            ),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
            ),
            atomic=False,
        ),
        **attrs,
    }
    return type("_BothView", (ServiceUpdateView,), namespace).as_view()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "attrs",
    [
        {"kwargs": lambda: {"team": "author-team"}},
        {"get_service_kwargs": lambda self: {"team": "author-team"}},
    ],
    ids=["spec-provider", "view-hook"],
)
def test_an_authors_value_for_a_hidden_lookup_key_is_seeded_over_the_capture(
    seen: list[Any], attrs: dict[str, Any]
) -> None:
    """Behaviour change: the service is handed the author's value, as it was, and
    the lookup now is too, where it was handed the capture: a call has one value
    for a seeded name."""
    post = Post.objects.create(title="p")

    response = _both_view(**attrs)(
        factory.put("/", {}, format="json"), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200
    assert seen == [("lookup", "author-team"), ("service", "author-team")]


class _TeamScope(TypedDict):
    team: str


def _scope_to_the_lookups_team() -> _TeamScope:
    return {"team": "lookup-team"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lookup_kwargs",
    [_scope_to_the_lookups_team, lambda: {"team": "lookup-team"}],
    ids=["typed", "untyped"],
)
def test_a_lookup_provider_answering_a_hidden_key_wins(seen: list[Any], lookup_kwargs: Any) -> None:
    """The lookup's own provider fills the name, so the capture is not seeded
    under it; an untyped one may answer any name, so nothing is seeded beside it
    (its ``untyped`` case)."""
    post = Post.objects.create(title="p")

    response = _both_view(lookup_kwargs=lookup_kwargs)(
        factory.put("/", {}, format="json"), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200
    assert seen == [("lookup", "lookup-team"), ("service", "service-default")]


class _TeamIn(serializers.Serializer):
    team = serializers.CharField()


def _echo_team(*, instance: Post, data: Any) -> Post:
    _SEEN.append(data["team"])
    return instance


class _FieldNamedLikeTheKeyView(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_echo_team,
        input_serializer=_TeamIn,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_for_team),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.mark.django_db
def test_a_route_capture_scopes_the_lookup_beside_a_field_of_its_name(seen: list[Any]) -> None:
    """The route is the author's, so its capture scopes the lookup that hides the
    name, as it always did; the serializer's field of that name is the client's,
    validated from the body into ``data``."""
    post = Post.objects.create(title="p")

    response = _FieldNamedLikeTheKeyView.as_view()(
        factory.put("/", {"team": "body-team"}, format="json"), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200, response.data
    assert seen == ["route-team", "body-team"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unknown_arguments",
    [UnknownArguments.IGNORE, UnknownArguments.REJECT],
    ids=["ignore", "reject"],
)
def test_a_field_named_like_a_captured_hidden_key_is_no_unexpected_argument(
    seen: list[Any], unknown_arguments: UnknownArguments
) -> None:
    """Off HTTP too, through a transport's offline view: the body's value is the
    serializer's field, never an argument a strict caller is refused for."""
    post = Post.objects.create(title="p")
    view = OfflineServiceView(
        request=Request(factory.put("/")), kwargs={"pk": post.pk, "team": "route-team"}
    )

    dispatch_spec(
        _FieldNamedLikeTheKeyView.spec,
        user=None,
        params={"team": "body-team"},
        view=view,
        unknown_arguments=unknown_arguments,
    )

    assert seen == ["route-team", "body-team"]


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


def _touch_with_team(*, instance: Post, team: str = "service-default") -> Post:
    _SEEN.append(team)
    return instance


class _ReadNamedLikeTheKeyView(ServiceUpdateView):
    queryset = Post.objects.all()
    spec = ServiceSpec(
        service=_touch_with_team,
        reads=(UrlKwarg("team"),),
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_for_team),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        atomic=False,
    )


@pytest.mark.django_db
def test_a_read_named_like_a_hidden_lookup_key_is_the_service_s_argument(
    seen: list[Any],
) -> None:
    """A read the spec declares is an argument, which a seed may not occupy, so the
    capture is not seeded beside it: the service reads it, and the lookup keeps its
    default."""
    post = Post.objects.create(title="p")

    response = _ReadNamedLikeTheKeyView.as_view()(
        factory.put("/", {}, format="json"), pk=post.pk, team="route-team"
    )

    assert response.status_code == 200, response.data
    assert seen == ["own-team", "route-team"]


def _create_for_tenant(*, tenant: Annotated[str, NotClientInput] = "server-tenant") -> Post:
    _SEEN.append(tenant)
    return Post.objects.create(title=tenant)


_TENANT_READ_HIDDEN_BY_ITS_SERVICE = ServiceSpec(
    service=_create_for_tenant,
    reads=(QueryParam("tenant"),),
    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer),
    atomic=False,
)


class _ReadHiddenByItsServiceView(ServiceCreateView):
    spec = _TENANT_READ_HIDDEN_BY_ITS_SERVICE


@pytest.mark.django_db
def test_a_read_its_own_service_hides_is_refused_over_http(seen: list[Any]) -> None:
    """The service marks the name the server's and the spec declares it the
    client's: refused, rather than handing the query string's value to the
    parameter the service keeps for itself."""
    with pytest.raises(ImproperlyConfigured, match="NotClientInput"):
        _ReadHiddenByItsServiceView.as_view()(factory.post("/?tenant=client", {}, format="json"))

    assert seen == []


@pytest.mark.django_db
@pytest.mark.parametrize("read", [QueryParam("tenant"), UrlKwarg("tenant")])
def test_a_read_its_own_service_hides_is_refused_off_http(
    seen: list[Any], read: QueryParam | UrlKwarg
) -> None:
    spec = ServiceSpec(service=_create_for_tenant, reads=(read,), atomic=False)

    with pytest.raises(ImproperlyConfigured, match="NotClientInput"):
        dispatch_spec(spec, user=None, params={"tenant": "client"})

    assert seen == []
