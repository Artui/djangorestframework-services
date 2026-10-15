"""An input serializer that reads ``self.context["request"]`` or ``["view"]`` unguarded.

DRF builds an input serializer with its view's context, so a serializer may
read the request or the view while it is built or while its fields are: a
``PrimaryKeyRelatedField`` scoped to the caller, a field only a staff caller
writes, a field a query value turns on. Dispatch describes the input serializer
to learn its fields, and describes it with the call's own request and view, so
each answers as the view always did, over HTTP and off it. The scope holds in
the serializer that validates: a caller picks a row of its own and is refused
another's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.pool.pool_seeds import PoolSeeds
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from rest_framework_services import (
    DispatchResult,
    OfflineServiceView,
    ServiceCreateView,
    ServiceSpec,
    ServiceViewSet,
    UrlKwarg,
    adispatch_spec,
    dispatch_spec,
    service_action,
)
from rest_framework_services.registry.spec_registry import SpecRegistry
from tests.testapp.models import Post

_factory = APIRequestFactory()


class _ScopedToTheCaller(serializers.Serializer):
    """A ``PrimaryKeyRelatedField`` scoped to the caller, set as the serializer is built."""

    post = serializers.PrimaryKeyRelatedField(queryset=Post.objects.none())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["post"].queryset = Post.objects.filter(title=user.username)


class _FieldsFromTheQuery(serializers.Serializer):
    """A field a query value turns on, read unguarded while the fields are built."""

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        if self.context["request"].query_params.get("extra"):
            fields["extra"] = serializers.CharField()
        return fields


class _FieldsFromTheView(serializers.Serializer):
    """A field named by the view's route, read unguarded while the fields are built."""

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields[self.context["view"].kwargs.get("field", "title")] = serializers.CharField()
        return fields


def _spec(serializer: type[serializers.Serializer], seen: dict[str, Any], **extra: Any) -> Any:
    def service(*, data: Any) -> None:
        seen["data"] = dict(data)

    return ServiceSpec(
        service=service, input_serializer=serializer, permission_classes=[], atomic=False, **extra
    )


@pytest.fixture
def owner(db: Any) -> User:
    return User.objects.create(username="owner")


@pytest.fixture
def own_post(owner: User) -> Post:
    return Post.objects.create(title="owner")


@pytest.fixture
def other_post(db: Any) -> Post:
    return Post.objects.create(title="someone-else")


def _create(spec: Any, body: dict[str, Any], user: User, url: str = "/") -> Any:
    request = _factory.post(url, body, format="json")
    force_authenticate(request, user=user)
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})
    return view.as_view()(request)


def _action(spec: Any, body: dict[str, Any], user: User, url: str = "/") -> Any:
    class _ViewSet(ServiceViewSet):
        permission_classes: list[Any] = []

        @service_action(detail=False, methods=["post"], spec=spec)
        def go(self, request: Any) -> None: ...

    request = _factory.post(url, body, format="json")
    force_authenticate(request, user=user)
    return _ViewSet.as_view({"post": "go"})(request)


_LIVE = pytest.mark.parametrize("live", [_create, _action], ids=["create-view", "service-action"])


@_LIVE
def test_a_live_view_scopes_the_field_to_the_caller(
    live: Callable[..., Any], owner: User, own_post: Post, other_post: Post
) -> None:
    seen: dict[str, Any] = {}
    spec = _spec(_ScopedToTheCaller, seen)

    assert live(spec, {"post": own_post.pk}, owner).status_code == 204
    assert seen == {"data": {"post": own_post}}

    refused = live(spec, {"post": other_post.pk}, owner)
    assert refused.status_code == 400
    assert refused.data["post"][0].code == "does_not_exist"


@_LIVE
def test_a_live_view_builds_the_fields_from_its_query_string(
    live: Callable[..., Any], owner: User
) -> None:
    seen: dict[str, Any] = {}

    response = live(_spec(_FieldsFromTheQuery, seen), {"extra": "e"}, owner, url="/?extra=1")

    assert response.status_code == 204, response.data
    assert seen == {"data": {"extra": "e"}}


@_LIVE
def test_a_live_view_builds_the_fields_from_its_route(
    live: Callable[..., Any], owner: User
) -> None:
    seen: dict[str, Any] = {}

    response = live(_spec(_FieldsFromTheView, seen), {"title": "t"}, owner)

    assert response.status_code == 204, response.data
    assert seen == {"data": {"title": "t"}}


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _offline(user: User, url: str = "/", **route: Any) -> OfflineServiceView:
    request = Request(_factory.post(url))
    request.user = user
    return OfflineServiceView(request=request, kwargs=route)


@_CORES
@pytest.mark.parametrize("reads", [(), (UrlKwarg("org"),)], ids=["no-reads", "a-url-kwarg"])
def test_an_offline_view_scopes_the_field_to_the_caller(
    dispatch: Callable[..., DispatchResult],
    reads: Any,
    owner: User,
    own_post: Post,
    other_post: Post,
) -> None:
    """The transports' path: a request and an ``OfflineServiceView`` they built."""
    seen: dict[str, Any] = {}
    spec = _spec(_ScopedToTheCaller, seen, reads=reads)

    view = _offline(owner)
    dispatch(spec, user=owner, params={"post": own_post.pk}, request=view.request, view=view)
    assert seen == {"data": {"post": own_post}}

    view = _offline(owner)
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, user=owner, params={"post": other_post.pk}, request=view.request, view=view)
    assert excinfo.value.detail["post"][0].code == "does_not_exist"


@_CORES
def test_an_offline_view_builds_the_fields_from_its_query_string_and_route(
    dispatch: Callable[..., DispatchResult], owner: User
) -> None:
    seen: dict[str, Any] = {}
    view = _offline(owner, "/?extra=1")

    dispatch(
        _spec(_FieldsFromTheQuery, seen),
        user=owner,
        params={"extra": "e"},
        request=view.request,
        view=view,
    )
    assert seen == {"data": {"extra": "e"}}

    view = _offline(owner, field="name")
    dispatch(
        _spec(_FieldsFromTheView, seen),
        user=owner,
        params={"name": "n"},
        request=view.request,
        view=view,
    )
    assert seen == {"data": {"name": "n"}}


@_CORES
def test_with_no_view_and_no_request_the_caller_is_the_principal(
    dispatch: Callable[..., DispatchResult], owner: User, own_post: Post, other_post: Post
) -> None:
    """The offline pair the serializer is bound with is the one it is described with."""
    seen: dict[str, Any] = {}
    spec = _spec(_ScopedToTheCaller, seen)

    dispatch(spec, user=owner, params={"post": own_post.pk})
    assert seen == {"data": {"post": own_post}}

    with pytest.raises(ValidationError):
        dispatch(spec, user=owner, params={"post": other_post.pk})


class _Guarded(serializers.Serializer):
    title = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and request.user.is_staff:
            fields["note"] = serializers.CharField()
        return fields


@_CORES
def test_a_guarded_serializer_with_no_view_and_no_request(
    dispatch: Callable[..., DispatchResult],
) -> None:
    seen: dict[str, Any] = {}

    dispatch(_spec(_Guarded, seen), user=None, params={"title": "t"})

    assert seen == {"data": {"title": "t"}}


class _RefusesAsTheCaller(serializers.Serializer):
    """Refuses in the caller's name, from a field built for the caller."""

    def get_fields(self) -> dict[str, serializers.Field]:
        user = self.context["request"].user
        return {"title": serializers.CharField(source="heading", help_text=user.username)}

    def validate_title(self, value: str) -> str:
        raise serializers.ValidationError(f"not for {self.fields['title'].help_text}")


@_CORES
def test_a_refusal_is_answered_in_the_wire_names(
    dispatch: Callable[..., DispatchResult], owner: User
) -> None:
    view = _offline(owner)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            _spec(_RefusesAsTheCaller, {}),
            user=owner,
            params={"title": "t"},
            request=view.request,
            view=view,
        )

    assert excinfo.value.detail == {"title": ["not for owner"]}


def test_a_spec_declaring_a_read_registers_and_mounts_at_import() -> None:
    """No call exists at import, so its fields cannot be read, and nothing is refused for that."""
    spec = _spec(_ScopedToTheCaller, {}, reads=(UrlKwarg("org"),))

    SpecRegistry().register("pick", spec)
    type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec}).as_view()


class _Broken(serializers.Serializer):
    def get_fields(self) -> dict[str, serializers.Field]:
        raise RuntimeError("the serializer is broken")


def test_a_serializer_that_fails_for_another_reason_is_not_hidden_at_registration() -> None:
    with pytest.raises(RuntimeError, match="the serializer is broken"):
        SpecRegistry().register("broken", _spec(_Broken, {}, reads=(UrlKwarg("org"),)))


@_CORES
def test_a_serializer_that_fails_is_not_hidden_at_dispatch(
    dispatch: Callable[..., DispatchResult], owner: User
) -> None:
    class _MissingKey(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            return {"title": serializers.CharField(), **self.context["absent"]}

    view = _offline(owner)
    with pytest.raises(KeyError, match="absent"):
        dispatch(_spec(_MissingKey, {}), user=owner, params={}, request=view.request, view=view)


def test_an_input_serializer_that_is_none_of_the_two_is_still_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="cannot be read as a serializer"):
        SpecRegistry().register("odd", _spec(cast(Any, object), {}, reads=(UrlKwarg("org"),)))


class _CountsItsBuilds(serializers.Serializer):
    """Writes to its context as it is built, which DRF hands each serializer fresh."""

    title = serializers.CharField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context["builds"] = self.context.get("builds", 0) + 1

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "builds": self.context["builds"]}


@_CORES
def test_each_serializer_of_the_call_has_its_own_context(
    dispatch: Callable[..., DispatchResult], owner: User
) -> None:
    """The input serializer is described before it validates, each with its own context."""
    seen: dict[str, Any] = {}
    view = _offline(owner)

    dispatch(
        _spec(_CountsItsBuilds, seen),
        user=owner,
        params={"title": "t"},
        request=view.request,
        view=view,
    )

    assert seen == {"data": {"title": "t", "builds": 1}}


class _RecordsItsContext(serializers.Serializer):
    title = serializers.CharField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "request": self.context["request"], "view": self.context["view"]}


@pytest.mark.parametrize("another", ["request", "view"], ids=["another-request", "another-view"])
def test_a_kernel_call_inside_the_service_has_its_own_context(another: str, owner: User) -> None:
    """A kernel call the service makes is built for the request and view it seeds."""
    outer = _offline(owner)
    other = _offline(owner)
    request = other.request if another == "request" else outer.request
    view = other if another == "view" else outer
    inner_seen: dict[str, Any] = {}
    inner = _spec(_RecordsItsContext, inner_seen)

    def service(*, data: Any) -> None:
        compiled = kernel_spec(inner)
        kernel_dispatch(
            compiled,
            principal=owner,
            arguments={"title": "inner"},
            grant=Grant(compiled, owner),
            pool_seeds=PoolSeeds(seeds=(("request", lambda: request), ("view", lambda: view))),
        )

    outer_spec = ServiceSpec(
        service=service, input_serializer=_RecordsItsContext, permission_classes=[], atomic=False
    )
    dispatch_spec(
        outer_spec, user=owner, params={"title": "outer"}, request=outer.request, view=outer
    )

    assert inner_seen["data"]["request"] is request
    assert inner_seen["data"]["view"] is view
