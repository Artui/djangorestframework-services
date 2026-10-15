"""A filter named like a parameter its selector marks ``NotClientInput``.

The filter reads the client's value, as every filter does, and the parameter
is the server's: the client's value never reaches it, and the server's (the
spec's ``kwargs`` provider, a route capture) never reaches the filter. The
parameter takes its default when the server gives it nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Post

_factory = APIRequestFactory()


class _ByTitle(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields = ["title"]


class _Out(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["title"]


def _selector(seen: list[str]) -> Callable[..., Any]:
    def every_post(title: Annotated[str, NotClientInput] = "unset") -> Any:
        seen.append(title)
        return Post.objects.order_by("title")

    return every_post


def _spec(selector: Callable[..., Any], **extra: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        filter_set=_ByTitle,
        output_serializer=_Out,
        permission_classes=[],
        **extra,
    )


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@pytest.fixture
def posts(db: Any) -> None:
    Post.objects.create(title="a")
    Post.objects.create(title="b")


def _titles(result: DispatchResult) -> list[str]:
    return [post.title for post in result.value]


@_CORES
def test_the_client_s_value_filters_and_never_reaches_the_parameter(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[str] = []

    result = dispatch(_spec(_selector(seen)), params={"title": "a"})

    assert _titles(result) == ["a"]
    assert seen == ["unset"]


@_CORES
def test_an_async_selector_s_parameter_is_kept_from_the_client_too(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[str] = []

    async def every_post(title: Annotated[str, NotClientInput] = "unset") -> Any:
        seen.append(title)
        return Post.objects.order_by("title")

    result = dispatch(_spec(every_post), params={"title": "a"})

    assert _titles(result) == ["a"]
    assert seen == ["unset"]


@_CORES
def test_the_provider_s_value_reaches_the_parameter_and_not_the_filter(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[str] = []
    spec = _spec(_selector(seen), kwargs=lambda: {"title": "b"})

    assert _titles(dispatch(spec, params={"title": "a"})) == ["a"]
    assert _titles(dispatch(spec, params={})) == ["a", "b"]
    assert seen == ["b", "b"]


@_CORES
def test_a_client_naming_the_server_s_channel_reaches_nothing(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    """However the server's value is carried to the parameter, a client cannot send it."""
    seen: list[str] = []
    spec = _spec(_selector(seen))

    for key in ("_server_owned_title", "title"):
        dispatch(spec, params={key: "a"})

    assert seen == ["unset", "unset"]


def _view(spec: Any) -> Any:
    return type(
        "_View",
        (SelectorListView,),
        {"spec": spec, "permission_classes": [], "authentication_classes": []},
    ).as_view()


def test_over_a_live_view_the_query_string_filters_and_never_reaches_the_parameter(
    posts: None,
) -> None:
    seen: list[str] = []

    response = _view(_spec(_selector(seen)))(_factory.get("/?title=a"))

    assert response.status_code == 200
    assert [row["title"] for row in response.data] == ["a"]
    assert seen == ["unset"]


def test_over_a_live_view_a_route_capture_reaches_the_parameter_and_not_the_filter(
    posts: None,
) -> None:
    seen: list[str] = []

    response = _view(_spec(_selector(seen)))(_factory.get("/b/?title=a"), title="b")

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == ["a"]
    assert seen == ["b"]


@_CORES
def test_a_target_lookup_s_parameter_is_kept_from_the_client_too(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[str] = []
    got: dict[str, Any] = {}

    def a_post(pk: int, title: Annotated[str, NotClientInput] = "unset") -> Any:
        seen.append(title)
        return Post.objects.filter(pk=pk)

    def service(*, instance: Any) -> None:
        got["instance"] = instance.title

    spec = ServiceSpec(
        service=service,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=a_post, filter_set=_ByTitle
        ),
        permission_classes=[],
        atomic=False,
    )
    pk = Post.objects.get(title="a").pk

    dispatch(spec, params={"pk": pk, "title": "a"})

    assert got == {"instance": "a"}
    assert seen == ["unset"]


def test_through_the_kernel_the_provider_s_value_reaches_the_parameter_and_not_the_filter(
    posts: None,
) -> None:
    """The compiled spec keeps the two apart for a transport dispatching it itself."""
    seen: list[str] = []
    compiled = kernel_spec(_spec(_selector(seen), kwargs=lambda: {"title": "b"}))
    principal = AnonymousUser()

    def call(arguments: dict[str, Any]) -> list[str]:
        result = kernel_dispatch(
            compiled, principal=principal, arguments=arguments, grant=Grant(compiled, principal)
        )
        return [post.title for post in result.value]

    assert call({"title": "a"}) == ["a"]
    assert seen == ["b"]
    # The channel is no argument, so a client naming it is refused as any unknown name is.
    with pytest.raises(InvalidArguments):
        call({"_server_owned_title": "a"})


@_CORES
def test_a_precondition_s_parameter_is_kept_from_the_client_too(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[str] = []
    gated: list[str] = []

    def gate(title: Annotated[str, NotClientInput] = "unset") -> None:
        gated.append(title)

    result = dispatch(_spec(_selector(seen), preconditions=[gate]), params={"title": "a"})

    assert _titles(result) == ["a"]
    assert gated == ["unset"]


def test_over_a_live_view_a_hook_s_value_reaches_the_parameter_and_not_the_filter(
    posts: None,
) -> None:
    seen: list[str] = []
    view = type(
        "_View",
        (SelectorListView,),
        {
            "spec": _spec(_selector(seen)),
            "permission_classes": [],
            "authentication_classes": [],
            "get_selector_kwargs": lambda self: {"title": "b"},
        },
    ).as_view()

    response = view(_factory.get("/?title=a"))

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == ["a"]
    assert seen == ["b"]
