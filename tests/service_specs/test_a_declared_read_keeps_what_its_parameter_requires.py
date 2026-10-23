"""A declared ``UrlKwarg`` or ``QueryParam`` keeps the requiredness of the parameter it names.

The read says where a value comes from. It does not say the callable can run
without one, so a read naming a parameter that has no default compiles required,
as it does with no read declared, and the kernel's input schema asks the caller
for what ``dispatch`` refuses the call without. A read stays optional where its
own ``default`` fills the parameter or the parameter has a default of its own,
and ``UrlKwarg(required=True)`` requires it either way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django_service_specs.schema.spec_input_schema import spec_input_schema
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework.permissions import AllowAny

from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg


def _in_project(user: Any, project_pk: int) -> list[Any]:
    return []


def _in_project_or_all(user: Any, project_pk: int = 0) -> list[Any]:
    return []


def _lookup(user: Any, pk: int) -> Any:
    return None


def _lookup_or_first(user: Any, pk: int = 1) -> Any:
    return None


def _renamed(*, note: str) -> Any:
    return note


def _selector(selector: Callable[..., Any], *reads: UrlKwarg | QueryParam) -> Any:
    return SelectorSpec(selector=selector, kind=SelectorKind.LIST, reads=reads)


def _service(
    lookup: Callable[..., Any],
    *,
    reads: tuple[UrlKwarg | QueryParam, ...] = (),
    lookup_reads: tuple[UrlKwarg | QueryParam, ...] = (),
    role: str = "instance",
) -> Any:
    kind = SelectorKind.RETRIEVE if role == "instance" else SelectorKind.LIST
    return ServiceSpec(
        service=lambda **kwargs: None,
        permission_classes=[AllowAny],
        reads=reads,
        **{f"{role}_selector_spec": SelectorSpec(kind=kind, selector=lookup, reads=lookup_reads)},
    )


def _spread(*reads: UrlKwarg | QueryParam) -> Any:
    return ServiceSpec(service=_renamed, permission_classes=[AllowAny], reads=reads)


def _own(spec: Any) -> Any:
    compiled = kernel_spec(spec)
    assert isinstance(compiled, KernelSelectorSpec)
    return compiled


def _lookup_of(spec: Any) -> Any:
    compiled = kernel_spec(spec)
    assert isinstance(compiled, KernelServiceSpec)
    target = compiled.target_selector_spec()
    assert isinstance(target, KernelSelectorSpec)
    return target


def _spread_compiled(spec: Any) -> Any:
    return as_kernel_spec(spec, spread=True)


# (compiled spec, name): each seat a declared read reaches.
_SEATS = [
    pytest.param(
        lambda: _own(_selector(_in_project, UrlKwarg("project_pk", type="integer"))),
        "project_pk",
        id="selector-url-kwarg",
    ),
    pytest.param(
        lambda: _own(_selector(_in_project, QueryParam("project_pk"))),
        "project_pk",
        id="selector-query-param",
    ),
    pytest.param(
        lambda: _lookup_of(_service(_lookup, reads=(QueryParam("pk"),))),
        "pk",
        id="lookup-read-of-the-spec",
    ),
    pytest.param(
        lambda: _lookup_of(_service(_lookup, lookup_reads=(QueryParam("pk"),), role="collection")),
        "pk",
        id="lookup-read-of-the-lookup",
    ),
    pytest.param(
        lambda: _spread_compiled(_spread(QueryParam("note"))),
        "note",
        id="spread-service",
    ),
]


@pytest.mark.parametrize(("compiled", "name"), _SEATS)
def test_a_read_naming_a_parameter_with_no_default_compiles_required(
    compiled: Callable[[], Any], name: str
) -> None:
    built = compiled()
    parameter = built.parameters().get(name)

    assert parameter is not None
    assert parameter.required is True
    # A declared read is the spec's, not read off the signature, so the kernel
    # still refuses it as an argument meeting a registered seed.
    assert parameter.reflected is False
    assert name in spec_input_schema(built)["required"]


# Kills the ``read.default`` operand: the parameter has none, so only the
# read's own default can keep it optional.
@pytest.mark.parametrize(
    ("compiled", "name"),
    [
        pytest.param(
            lambda: _own(_selector(_in_project, UrlKwarg("project_pk", type="integer", default=7))),
            "project_pk",
            id="selector-url-kwarg",
        ),
        pytest.param(
            lambda: _own(_selector(_in_project, QueryParam("project_pk", default="7"))),
            "project_pk",
            id="selector-query-param",
        ),
        pytest.param(
            lambda: _lookup_of(_service(_lookup, reads=(QueryParam("pk", default="1"),))),
            "pk",
            id="lookup-read-of-the-spec",
        ),
    ],
)
def test_a_read_with_a_default_stays_optional(compiled: Callable[[], Any], name: str) -> None:
    built = compiled()

    assert built.parameters().get(name).required is False
    assert name not in spec_input_schema(built).get("required", [])


# Kills the ``reflected.required`` operand: the read says nothing of
# requiredness, so the parameter's own default is what keeps it optional.
@pytest.mark.parametrize(
    ("compiled", "name"),
    [
        pytest.param(
            lambda: _own(_selector(_in_project_or_all, QueryParam("project_pk"))),
            "project_pk",
            id="selector",
        ),
        pytest.param(
            lambda: _lookup_of(_service(_lookup_or_first, reads=(QueryParam("pk"),))),
            "pk",
            id="lookup",
        ),
    ],
)
def test_a_parameter_with_a_default_stays_optional_under_a_read(
    compiled: Callable[[], Any], name: str
) -> None:
    built = compiled()

    assert built.parameters().get(name).required is False
    assert name not in spec_input_schema(built).get("required", [])


# Kills the ``read.required`` operand: the parameter is optional by itself,
# so only the read can make it required.
def test_a_read_saying_required_requires_a_parameter_that_has_a_default() -> None:
    built = _own(_selector(_in_project_or_all, UrlKwarg("project_pk", required=True)))

    assert built.parameters().get("project_pk").required is True
    assert "project_pk" in spec_input_schema(built)["required"]
