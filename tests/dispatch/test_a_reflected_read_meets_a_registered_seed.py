"""What the kernel does with a seed's name once drfs marks the reads a signature gave.

Two seats, each held through the kernel's own ``dispatch`` of the default
compile (``kernel_spec``), which is how a transport that is not the DRF edge
reaches a drfs spec:

- a selector's, a target lookup's or an ``Unpack``-ed key's parameter named like
  a registered seed is the seed's (``Parameter.reflected``);
- an input serializer's field named like one is validated into ``data`` and
  never spread into the service's pool, so the service's parameter of that name
  is the seed's.

And the DRF edge, which dispatches the same specs per request, answers a target
lookup or a spread service reading a seed beside such a field as it always did.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ArgumentBinding,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    dispatch_spec,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from tests.testapp.models import Post

_SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


class _Keys(TypedDict, total=False):
    tenant: str


class _TenantFieldIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()


def _through_the_kernel(
    spec: Any,
    arguments: dict[str, Any],
    *,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
) -> Any:
    compiled = kernel_spec(spec)
    principal = AnonymousUser()
    return kernel_dispatch(
        compiled,
        principal=principal,
        arguments=arguments,
        grant=Grant(compiled, principal),
        pool_seeds=_SEEDS,
        unknown_arguments=unknown_arguments,
    ).value


def _scope(*, tenant: str) -> dict[str, str]:
    return {"tenant": tenant}


def _unpacked(**keys: Unpack[_Keys]) -> dict[str, Any]:
    return {"tenant": keys["tenant"]}


@pytest.mark.parametrize("selector", [_scope, _unpacked], ids=["named", "unpacked"])
def test_the_kernel_fills_a_selector_s_reflected_read_from_the_seed(selector: Any) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, permission_classes=[])

    assert _through_the_kernel(spec, {}) == {"tenant": "acme"}


@pytest.mark.parametrize("selector", [_scope, _unpacked], ids=["named", "unpacked"])
def test_a_caller_naming_the_seed_never_fills_a_reflected_read(selector: Any) -> None:
    """The read is no argument of the call: dropped under ``IGNORE``, unknown under ``REJECT``."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, permission_classes=[])

    assert _through_the_kernel(
        spec, {"tenant": "other"}, unknown_arguments=UnknownArguments.IGNORE
    ) == {"tenant": "acme"}
    with pytest.raises(KernelInvalidArguments):
        _through_the_kernel(spec, {"tenant": "other"})


@pytest.mark.django_db
def test_the_kernel_fills_a_target_lookup_s_reflected_read_from_the_seed() -> None:
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

    assert _through_the_kernel(spec, {"pk": post.pk}) == "a"
    assert seen == ["acme"]


def test_a_declared_read_named_like_the_seed_is_still_refused_by_the_kernel() -> None:
    """A mark on every read, a declared one included, fails this: the seed would fill it."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_scope,
        reads=[QueryParam("tenant")],
        permission_classes=[],
    )

    with pytest.raises(ImproperlyConfigured, match="tenant"):
        _through_the_kernel(spec, {})


def test_a_field_named_like_a_seed_validates_into_data_through_the_kernel() -> None:
    """The service's ``tenant`` is the seed's and ``data`` carries the caller's.

    Kernel 0.14.0 refuses the spec in ``open_call`` because the Validator lists
    the field as a parameter beside the seed.
    """
    seen: dict[str, Any] = {}

    def service(*, data: Any, tenant: str) -> None:
        seen["data"] = dict(data)
        seen["tenant"] = tenant

    spec = ServiceSpec(service=service, input_serializer=_TenantFieldIn, atomic=False)

    _through_the_kernel(spec, {"title": "t", "tenant": "client"})

    assert seen == {"data": {"title": "t", "tenant": "client"}, "tenant": "acme"}


# --- the DRF edge answers as it did before the kernel filled a marked read ---------------
#
# Pinned against the answers the edge gave while it compiled a seed's name out of the
# reads for each call, which the kernel's mark made unnecessary: the lookup reads the
# seed beside a field of its name, and a spread service names it beside a lookup that
# reads it.


def _a_post(seen: dict[str, Any]) -> Any:
    def a_post(pk: int, *, tenant: str) -> Any:
        seen["lookup"] = tenant
        return Post.objects.filter(pk=pk)

    return a_post


_POLICIES = pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT], ids=["ignore", "reject"]
)


@pytest.mark.django_db
@_POLICIES
def test_a_lookup_reading_a_seed_beside_a_field_of_its_name_answers_as_before(
    policy: UnknownArguments,
) -> None:
    seen: dict[str, Any] = {}

    def rename(*, instance: Post, data: Any, tenant: str) -> dict[str, Any]:
        return {"data": dict(data), "tenant": tenant}

    spec = ServiceSpec(
        service=rename,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_a_post(seen)),
        input_serializer=_TenantFieldIn,
        permission_classes=[],
        atomic=False,
    )
    post = Post.objects.create(title="a")
    params = {"pk": post.pk, "title": "x", "tenant": "client"}

    result = dispatch_spec(
        spec, user=None, pool_seeds=_SEEDS, params=params, unknown_arguments=policy
    )

    # The body fills the field, the seed fills both reads of the name.
    assert result.value == {"data": {"title": "x", "tenant": "client"}, "tenant": "acme"}
    assert seen == {"lookup": "acme"}

    # The field is the serializer's to demand, so leaving it out is its refusal.
    with pytest.raises(InvalidArguments) as excinfo:
        dispatch_spec(
            spec,
            user=None,
            pool_seeds=_SEEDS,
            params={"pk": post.pk, "title": "x"},
            unknown_arguments=policy,
        )
    assert excinfo.value.get_codes() == {"tenant": ["required"]}


@pytest.mark.django_db
@_POLICIES
def test_a_spread_service_beside_a_lookup_reading_a_seed_answers_as_before(
    policy: UnknownArguments,
) -> None:
    seen: dict[str, Any] = {}

    def tag(*, instance: Post, title: str, tenant: str) -> dict[str, str]:
        return {"title": title, "tenant": tenant}

    spec = ServiceSpec(
        service=tag,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_a_post(seen)),
        permission_classes=[],
        atomic=False,
    )
    post = Post.objects.create(title="a")

    result = dispatch_spec(
        spec,
        user=None,
        pool_seeds=_SEEDS,
        params={"pk": post.pk, "title": "t"},
        unknown_arguments=policy,
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert result.value == {"title": "t", "tenant": "acme"}
    assert seen == {"lookup": "acme"}


@pytest.mark.django_db
@_POLICIES
def test_a_spread_service_naming_a_seed_refuses_a_value_the_caller_sent_as_before(
    policy: UnknownArguments,
) -> None:
    """Pinned as 0.59.0 on kernel 0.14.0 answers it, which is not a refusal worded for the caller."""
    seen: dict[str, Any] = {}

    def tag(*, instance: Post, title: str, tenant: str) -> dict[str, str]:
        return {"title": title, "tenant": tenant}

    spec = ServiceSpec(
        service=tag,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_a_post(seen)),
        permission_classes=[],
        atomic=False,
    )
    post = Post.objects.create(title="a")

    with pytest.raises(ImproperlyConfigured, match="dispatch seeds itself"):
        dispatch_spec(
            spec,
            user=None,
            pool_seeds=_SEEDS,
            params={"pk": post.pk, "title": "t", "tenant": "client"},
            unknown_arguments=policy,
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
