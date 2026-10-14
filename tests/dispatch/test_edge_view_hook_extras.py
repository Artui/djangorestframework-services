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
    """The view layers sit beneath the spec's own provider; a key that provider
    fills stays an extra, which the kernel drops beneath it."""
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
