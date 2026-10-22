"""A read taken off a signature is marked ``reflected``; a declared one is not.

The kernel fills a registered pool seed's name in a ``reflected`` read, so the
mark is what lets ``kernel_spec(spec)``, the default compile any transport
dispatches, read a seed in a selector's signature. A declared ``UrlKwarg`` or
``QueryParam`` is the author's own word and stays unmarked, so the kernel still
refuses one named like a seed. Each case reads the compile through its public
entry points.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.specs.kernel_spec import kernel_spec
from typing_extensions import TypedDict, Unpack

from rest_framework_services import QueryParam, SelectorKind, SelectorSpec, ServiceSpec, UrlKwarg
from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec


class _Keys(TypedDict, total=False):
    region: str


def _named(parameters: Any) -> dict[str, Parameter]:
    return {parameter.name: parameter for parameter in parameters}


def _lookup(pk: int, *, tenant: str) -> Any:
    raise AssertionError("compiled, never called")


def _selector(*, tenant: str, page: int = 1, **keys: Unpack[_Keys]) -> Any:
    raise AssertionError("compiled, never called")


def _service(*, tenant: str, title: str = "") -> None:
    raise AssertionError("compiled, never called")


def test_a_selector_s_parameters_and_unpacked_keys_are_marked() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_selector, permission_classes=[])

    reads = _named(kernel_spec(spec).reads)

    assert {name: read.reflected for name, read in reads.items()} == {
        "tenant": True,
        "page": True,
        "region": True,
    }


def test_a_target_lookup_s_parameters_are_marked() -> None:
    spec = ServiceSpec(
        service=lambda *, instance: None,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        permission_classes=[],
    )
    lookup = kernel_spec(spec).target_selector_spec()
    assert lookup is not None

    assert {name: read.reflected for name, read in _named(lookup.reads).items()} == {
        "pk": True,
        "tenant": True,
    }


def test_a_spread_service_s_parameters_are_marked() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[], atomic=False)

    reads = _named(as_kernel_spec(spec, spread=True).reads)

    assert {name: read.reflected for name, read in reads.items()} == {"tenant": True, "title": True}


@pytest.mark.parametrize("declared", [QueryParam, UrlKwarg], ids=["query-param", "url-kwarg"])
def test_a_declared_read_is_not_marked_and_replaces_the_reflected_one(declared: Any) -> None:
    """Marking every ``_parameter`` result fails this: the declared read would carry the mark."""
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_selector,
        reads=[declared("tenant")],
        permission_classes=[],
    )

    reads = _named(kernel_spec(spec).reads)

    assert reads["tenant"].reflected is False
    assert reads["page"].reflected is True
