"""A view hook's extras at the DRF edge, on both cores.

A view's ``get_*_kwargs`` hooks have always supplied values for the parameters
a spec declares: that is what ``get_selector_kwargs()`` is for. The kernel
refuses ``extra_kwargs=`` naming a declared parameter, so the edge routes a
hook value for a declared read into the call's arguments, where it wins over
the client's, and hands the rest to ``extra_kwargs``. Each test is written
against the public signatures, so it runs against the edge before that routing
too, where the declared ones fail with ``ImproperlyConfigured``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from typing_extensions import TypedDict

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _tenant_rows(*, tenant: str) -> list[str]:
    return [tenant]


_TENANT_LIST = SelectorSpec(kind=SelectorKind.LIST, selector=_tenant_rows)


@_CORES
def test_a_hook_value_for_a_selector_read_reaches_the_selector(dispatch: Dispatch) -> None:
    result = dispatch(
        _TENANT_LIST, params={}, view_hooks=ViewHooks(extra_kwargs={"tenant": "acme"})
    )

    assert result.value == ["acme"]


@_CORES
def test_a_hook_value_wins_over_the_clients_for_the_same_read(dispatch: Dispatch) -> None:
    result = dispatch(
        _TENANT_LIST,
        params={"tenant": "client"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "acme"}),
    )

    assert result.value == ["acme"]


@_CORES
def test_an_unset_hook_value_leaves_the_clients(dispatch: Dispatch) -> None:
    """``UNSET`` supplies nothing, so it is never routed in as a value."""
    result = dispatch(
        _TENANT_LIST,
        params={"tenant": "client"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": UNSET}),
    )

    assert result.value == ["client"]


def _posts_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post) -> int:
    return instance.pk


_TOUCH = ServiceSpec(
    service=_touch,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_posts_by_pk),
    atomic=False,
)


@_CORES
@pytest.mark.django_db
def test_a_hook_value_for_a_target_read_picks_the_row(dispatch: Dispatch) -> None:
    sent = Post.objects.create(title="sent")
    scoped = Post.objects.create(title="scoped")

    result = dispatch(
        _TOUCH, params={"pk": sent.pk}, view_hooks=ViewHooks(extra_kwargs={"pk": scoped.pk})
    )

    assert result.service_result == scoped.pk


def _greet(*, name: str) -> str:
    return f"hello {name}"


@_CORES
def test_a_hook_value_for_a_service_read_reaches_the_service(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_greet, atomic=False)

    result = dispatch(
        spec, params={"name": "client"}, view_hooks=ViewHooks(extra_kwargs={"name": "author"})
    )

    assert result.service_result == "hello author"


def _scoped_rows(*, tenant: Annotated[str, NotClientInput]) -> list[str]:
    return [tenant]


@_CORES
def test_a_hook_value_for_a_hidden_name_still_goes_as_an_extra(dispatch: Dispatch) -> None:
    """A ``NotClientInput`` name is no read, so it stays an ``extra_kwargs`` key:
    routed into the arguments it would be an argument nothing declares, which
    ``REJECT`` refuses."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_scoped_rows)

    result = dispatch(
        spec,
        params={},
        unknown_arguments=UnknownArguments.REJECT,
        view_hooks=ViewHooks(extra_kwargs={"tenant": "acme"}),
    )

    assert result.value == ["acme"]


class _TenantKwargs(TypedDict):
    tenant: str


def _tenant_from_the_spec() -> _TenantKwargs:
    return {"tenant": "spec"}


@_CORES
def test_the_specs_typed_provider_wins_over_a_hook_value(dispatch: Dispatch) -> None:
    """The view layers sit beneath the spec's own provider: the kernel drops any
    caller's value for a name that provider fills, a hook's routed one included."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_tenant_rows, kwargs=_tenant_from_the_spec)

    result = dispatch(
        spec,
        params={"tenant": "client"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    assert result.value == ["spec"]


@_CORES
def test_arguments_that_are_no_object_are_refused_as_the_clients_error(
    dispatch: Dispatch,
) -> None:
    """A hook value has nowhere to go in a list, and the list is the caller's
    400, never the author's ``ImproperlyConfigured``."""
    with pytest.raises(ValidationError):
        dispatch(
            _TENANT_LIST, params=["acme"], view_hooks=ViewHooks(extra_kwargs={"tenant": "acme"})
        )


_SEEDED_NAMES = pytest.mark.parametrize("name", ["request", "view", "progress", "user", "clock"])


@_CORES
@_SEEDED_NAMES
def test_a_hook_value_naming_a_seed_is_dropped_and_the_seed_wins(
    dispatch: Dispatch, name: str
) -> None:
    """Never overlaid and never refused: the kernel refuses an overlay naming a
    seed, so the edge leaves the seed's value alone."""
    seen: dict[str, Any] = {}

    def rows(**pool: Any) -> list[Any]:
        seen.update(pool)
        return []

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=rows)
    seeds = DEFAULT_POOL_SEEDS.extend(clock=lambda: "the-seed")

    dispatch(
        spec,
        params={},
        pool_seeds=seeds,
        view_hooks=ViewHooks(extra_kwargs={name: "from-the-hook"}),
    )

    assert seen[name] != "from-the-hook"


class _TitleTenantIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()

    def validate(self, attrs: Any) -> Any:
        return {**attrs, "context_tenant": self.context.get("tenant")}


def _echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


@pytest.mark.django_db
@_CORES
def test_a_hook_value_naming_a_serializer_field_reaches_its_context(dispatch: Dispatch) -> None:
    """Not an overlay, which the kernel refuses for a Validator field, and not the
    field's value, which stays the caller's: the serializer's context."""
    spec = ServiceSpec(service=_echo, input_serializer=_TitleTenantIn, atomic=False)

    result = dispatch(
        spec,
        params={"title": "t", "tenant": "client"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    assert result.value == {"title": "t", "tenant": "client", "context_tenant": "hook"}


@pytest.mark.django_db
@_CORES
def test_the_views_own_context_layer_wins_over_a_moved_field_value(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_echo, input_serializer=_TitleTenantIn, atomic=False)

    result = dispatch(
        spec,
        params={"title": "t", "tenant": "client"},
        view_hooks=ViewHooks(
            extra_kwargs={"tenant": "hook"}, input_serializer_context={"tenant": "context"}
        ),
    )

    assert result.value["context_tenant"] == "context"


@pytest.mark.django_db
@_CORES
def test_a_hook_value_naming_no_field_stays_an_extra_beside_a_serializer(
    dispatch: Dispatch,
) -> None:
    def service(*, data: Any, clock: str) -> dict[str, Any]:
        return {**data, "clock": clock}

    spec = ServiceSpec(service=service, input_serializer=_TitleTenantIn, atomic=False)

    result = dispatch(
        spec,
        params={"title": "t", "tenant": "client"},
        view_hooks=ViewHooks(extra_kwargs={"clock": "hook"}),
    )

    assert result.value["clock"] == "hook"
    assert result.value["context_tenant"] is None
