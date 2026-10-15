"""A selector reads a registered pool seed as a service does.

A parameter named like a seed the call registers is the seed's, so it is no
argument of the call: the seed fills it, a client's value of the name never
does, and the call is not refused for a parameter meeting a seed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ArgumentBinding,
    DispatchResult,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.registry.spec_registry import SpecRegistry
from tests.testapp.models import Post

_SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


def _scope(*, tenant: str) -> dict[str, str]:
    return {"tenant": tenant}


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, pool_seeds=_SEEDS, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, pool_seeds=_SEEDS, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _selector_spec() -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scope, permission_classes=[])


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("kind", ["selector", "service"])
def test_both_spec_kinds_read_the_seed(dispatch: Callable[..., DispatchResult], kind: str) -> None:
    spec = (
        _selector_spec()
        if kind == "selector"
        else ServiceSpec(service=_scope, permission_classes=[], atomic=False)
    )

    assert dispatch(spec, params={}).value == {"tenant": "acme"}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
def test_a_client_naming_the_seed_never_fills_it(
    dispatch: Callable[..., DispatchResult], policy: UnknownArguments
) -> None:
    result = dispatch(_selector_spec(), params={"tenant": "other"}, unknown_arguments=policy)

    assert result.value == {"tenant": "acme"}


@pytest.mark.django_db
@_CORES
def test_a_target_lookup_reads_the_seed(dispatch: Callable[..., DispatchResult]) -> None:
    seen: list[str] = []

    def a_post(pk: int, *, tenant: str) -> Any:
        seen.append(tenant)
        return Post.objects.filter(pk=pk)

    def rename(*, instance: Post) -> str:
        return instance.title

    spec = ServiceSpec(
        service=rename,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=a_post),
        permission_classes=[],
        atomic=False,
    )
    post = Post.objects.create(title="a")

    assert dispatch(spec, params={"pk": post.pk}).value == "a"
    assert seen == ["acme"]


@pytest.mark.django_db
@_CORES
def test_a_spread_service_reads_the_seed(dispatch: Callable[..., DispatchResult]) -> None:
    def tag(*, title: str, tenant: str) -> dict[str, str]:
        return {"title": title, "tenant": tenant}

    spec = ServiceSpec(service=tag, permission_classes=[], atomic=False)
    result = dispatch(
        spec, params={"title": "t"}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS
    )

    assert result.value == {"title": "t", "tenant": "acme"}


def _registered(registry: SpecRegistry, name: str) -> Any:
    entry = registry.get(name)
    assert entry is not None
    return entry.spec


@pytest.mark.django_db
@_CORES
def test_a_registered_spec_reads_the_seed_too(dispatch: Callable[..., DispatchResult]) -> None:
    registry = SpecRegistry()
    registry.register("scope", _selector_spec())

    assert dispatch(_registered(registry, "scope"), params={}).value == {"tenant": "acme"}


@pytest.mark.django_db
@_CORES
def test_a_declared_read_named_like_the_seed_is_still_refused(
    dispatch: Callable[..., DispatchResult],
) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_scope,
        reads=[QueryParam("tenant")],
        permission_classes=[],
    )

    with pytest.raises(ImproperlyConfigured, match="tenant"):
        dispatch(spec, params={})


@pytest.mark.parametrize("declared", [False, True], ids=["reflected", "declared"])
def test_the_kernel_s_dispatch_knows_no_project_seed_and_refuses_the_parameter(
    declared: bool,
) -> None:
    """``kernel_spec`` is compiled once, with no project's seeds: only ``dispatch_spec`` reads them."""
    spec = (
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_scope,
            reads=[QueryParam("tenant")],
            permission_classes=[],
        )
        if declared
        else _selector_spec()
    )
    compiled = kernel_spec(spec)
    principal = AnonymousUser()

    with pytest.raises(ImproperlyConfigured, match="tenant"):
        kernel_dispatch(
            compiled,
            principal=principal,
            arguments={},
            grant=Grant(compiled, principal),
            pool_seeds=_SEEDS,
        )
