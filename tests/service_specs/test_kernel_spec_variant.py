"""``kernel_spec_variant``: the compiled forms the DRF edge dispatches, each compiled once."""

from __future__ import annotations

import gc
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from rest_framework_services.service_specs import kernel_spec_variant as module
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from tests.testapp.models import Post


class _Note(serializers.Serializer):
    title = serializers.CharField()


def _update(*, instance: Any) -> Any:
    return instance


def _lookup(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _first() -> Any:
    return Post.objects.all()[:1]


def _service(compiled: Any) -> KernelServiceSpec:
    assert isinstance(compiled, KernelServiceSpec)
    return compiled


def test_the_default_variant_is_the_kernel_s_compiled_spec() -> None:
    spec = ServiceSpec(service=_update, permission_classes=[AllowAny])

    assert kernel_spec_variant(spec) is kernel_spec(spec)


def test_each_variant_is_compiled_once_per_source() -> None:
    spec = ServiceSpec(service=_update, permission_classes=[AllowAny])
    twin = ServiceSpec(service=_update, permission_classes=[AllowAny])

    first = kernel_spec_variant(spec, unguarded=True)

    assert kernel_spec_variant(spec, unguarded=True) is first
    assert kernel_spec_variant(spec, unguarded=True, target=True) is not first
    # Equal declarations are two sources: a Grant binds the compiled spec by identity.
    assert kernel_spec_variant(twin, unguarded=True) is not first


def test_a_source_s_variants_go_with_it() -> None:
    spec = ServiceSpec(service=_update)
    kernel_spec_variant(spec, unguarded=True)
    source_id = id(spec)
    assert source_id in module._VARIANTS

    del spec
    gc.collect()

    assert source_id not in module._VARIANTS


def test_unguarded_drops_the_permissions_and_nothing_else() -> None:
    spec = ServiceSpec(service=_update, permission_classes=[AllowAny], input_serializer=_Note)

    compiled = _service(kernel_spec_variant(spec, unguarded=True))

    assert compiled.permissions is None
    assert compiled.validator is _service(kernel_spec(spec)).validator


@pytest.mark.parametrize(("declared", "override"), [(None, True), (True, False)])
def test_partial_compiles_the_request_s_flag(declared: bool | None, override: bool) -> None:
    spec = ServiceSpec(service=_update, input_serializer=_Note, partial=declared)

    assert _service(kernel_spec_variant(spec, partial=override)).partial is override


@pytest.mark.django_db
def test_a_spec_with_no_lookup_takes_the_row_the_edge_hands_it() -> None:
    post = Post.objects.create(title="t")
    spec = ServiceSpec(service=_update, permission_classes=[])

    result = dispatch(
        kernel_spec_variant(spec, target=True), principal=AnonymousUser(), arguments={}, target=post
    )

    assert result.value == post


def test_the_stand_in_is_never_a_lookup() -> None:
    spec = ServiceSpec(service=_update, permission_classes=[])
    stand_in = _service(kernel_spec_variant(spec, target=True)).instance_selector_spec

    assert stand_in is not None
    assert tuple(stand_in.reads) == ()
    with pytest.raises(ImproperlyConfigured, match="dispatch it with target="):
        stand_in.selector()


def test_a_declared_lookup_is_kept() -> None:
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup)
    spec = ServiceSpec(service=_update, instance_selector_spec=lookup)

    compiled = _service(kernel_spec_variant(spec, target=True))

    assert compiled.instance_selector_spec is not None
    assert compiled.instance_selector_spec.selector.__wrapped__ is _lookup


def test_a_selector_spec_takes_no_stand_in() -> None:
    # A selector asking for no argument, so the variant has no read to relax
    # either, and changes nothing.
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_first)

    compiled = kernel_spec_variant(spec, target=True)

    assert isinstance(compiled, KernelSelectorSpec)
    assert compiled is kernel_spec(spec)
