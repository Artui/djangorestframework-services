"""A spreading service with no input serializer advertises its own parameters.

Under a ``SPREAD_*`` binding, dispatch declares a serializer-less service's own
parameters as its input (``_spread_parameter_keys``), so ``REJECT`` admits them. The
input schema ``spec_to_json_schema(spec, argument_binding=...)`` lists the same set,
reflected the way a selector's parameters are, so what a transport advertises and
what dispatch admits cannot drift apart. ``AUTO``, the default, is ``BUNDLE`` for a
service, which spreads nothing, and keeps the schema it always had.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import pytest
from rest_framework import serializers
from typing_extensions import TypedDict, Unpack

from rest_framework_services.dispatch.utils import _spread_parameter_keys
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

_SPREADS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)


def _spec(service: Any, **kwargs: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=service, atomic=False, **kwargs)


# --- the matrix ---------------------------------------------------------------


def _plain(*, reason: str, note: str) -> None: ...


def _defaulted(*, reason: str, note: str = "none") -> None: ...


def _hidden(*, reason: str, tenant: Annotated[str, NotClientInput()]) -> None: ...


def _seeded(*, reason: str, user: Any, data: Any, instance: Any, progress: Any) -> None: ...


def _viewed(*, reason: str, view: Any) -> None: ...


class _Extras(TypedDict, total=False):
    colour: str
    size: int
    team: Annotated[str, NotClientInput()]


def _unpacked(*, reason: str, **extras: Unpack[_Extras]) -> None: ...


def _positional(token: str, /, *, reason: str) -> None: ...


def _bare(*, reason: str, **changes) -> None: ...  # unannotated on purpose


_MATRIX = pytest.mark.parametrize(
    ("service", "expected"),
    [
        pytest.param(_plain, {"reason", "note"}, id="plain"),
        pytest.param(_defaulted, {"reason", "note"}, id="defaulted"),
        pytest.param(_hidden, {"reason"}, id="not-client-input"),
        pytest.param(_seeded, {"reason"}, id="reserved-seeds"),
        pytest.param(_viewed, {"reason"}, id="view"),
        pytest.param(_unpacked, {"reason", "colour", "size"}, id="unpack-typed-dict"),
        pytest.param(_positional, {"reason"}, id="positional-only"),
    ],
)


@_SPREADS
@_MATRIX
def test_the_properties_are_the_keys_dispatch_declares(
    binding: ArgumentBinding, service: Any, expected: set[str]
) -> None:
    spec = _spec(service)

    schema = spec_to_json_schema(spec, argument_binding=binding)
    assert schema is not None
    declared = _spread_parameter_keys(spec, argument_binding=binding, reserved=RESERVED_POOL_SEEDS)

    assert set(schema.get("properties", {})) == declared == expected


@_SPREADS
def test_a_bare_var_keyword_lists_what_it_names_and_closes_nothing(
    binding: ArgumentBinding,
) -> None:
    """Dispatch declares an open set, so every key the caller sends is admitted.
    The schema lists the parameters the service names and states no closure: drfs
    writes ``additionalProperties: false`` only for a ``many`` wrapper, and a
    transport closes or opens a tool from its own policy."""
    spec = _spec(_bare)

    schema = spec_to_json_schema(spec, argument_binding=binding)

    assert (
        _spread_parameter_keys(spec, argument_binding=binding, reserved=RESERVED_POOL_SEEDS) is None
    )
    assert schema == {"type": "object", "properties": {"reason": {"type": "string"}}}


def _names_nothing(*, user: Any, view: Any) -> None: ...


@_SPREADS
def test_a_service_naming_no_caller_parameter_lists_none(binding: ArgumentBinding) -> None:
    # No empty ``properties`` either: the object stays the bare one it always was.
    spec = _spec(_names_nothing)
    assert (
        _spread_parameter_keys(spec, argument_binding=binding, reserved=RESERVED_POOL_SEEDS)
        == set()
    )
    assert spec_to_json_schema(spec, argument_binding=binding) == {"type": "object"}


def _ticket_by_pk(*, pk: int) -> Any: ...


def _close_ticket(*, instance: Any, reason: str, note: str = "", user: Any) -> Any: ...


def test_the_documented_example_lists_the_parameters_and_not_the_lookup() -> None:
    # docs/reference/jsonschema.md: the lookup's ``pk`` is the transport's to merge.
    spec = _spec(
        _close_ticket,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_ticket_by_pk),
    )
    assert spec_to_json_schema(spec, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS) == {
        "type": "object",
        "properties": {"reason": {"type": "string"}, "note": {"type": "string"}},
    }


def _close_ticket_recipe(*, instance: Any, reason: str, notify: bool = False) -> Any: ...


def test_the_recipes_example_lists_the_parameters_and_not_the_lookup() -> None:
    # docs/recipes/off-http-inputs.md, "A spread service with no input serializer".
    spec = _spec(
        _close_ticket_recipe,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_ticket_by_pk),
    )
    assert spec_to_json_schema(spec, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS) == {
        "type": "object",
        "properties": {"reason": {"type": "string"}, "notify": {"type": "boolean"}},
    }
    assert spec_to_json_schema(spec) == {"type": "object"}


# --- requiredness ---------------------------------------------------------------


def _marked(*, reason: Annotated[str, InputRequired()], note: str) -> None: ...


@_SPREADS
def test_without_supplied_only_a_marker_requires(binding: ArgumentBinding) -> None:
    # As for a selector: a reader that does not know what the transport fills cannot
    # tell a caller's input from a pool value, so a missing default proves nothing.
    assert "required" not in spec_to_json_schema(_spec(_plain), argument_binding=binding)
    schema = spec_to_json_schema(_spec(_marked), argument_binding=binding)
    assert schema["required"] == ["reason"]


@_SPREADS
def test_under_supplied_a_parameter_with_no_default_is_required(binding: ArgumentBinding) -> None:
    schema = spec_to_json_schema(_spec(_defaulted), argument_binding=binding, supplied=frozenset())
    assert schema["required"] == ["reason"]


@_SPREADS
def test_a_supplied_name_is_not_advertised(binding: ArgumentBinding) -> None:
    schema = spec_to_json_schema(
        _spec(_plain), argument_binding=binding, supplied=frozenset({"note"})
    )
    assert schema == {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    }


# --- what keeps the schema it had ------------------------------------------------


class _ReasonInput(serializers.Serializer):
    reason = serializers.CharField()


_REASON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reason": {"type": "string"}},
    "required": ["reason"],
}


@_SPREADS
def test_a_serializer_still_declares_the_input(binding: ArgumentBinding) -> None:
    # Holds ``spec.input_serializer is None``: the serializer reads the input there,
    # and ``_spread_parameter_keys`` declares nothing beside it.
    spec = _spec(_plain, input_serializer=_ReasonInput)
    assert spec_to_json_schema(spec, argument_binding=binding) == _REASON_SCHEMA


def test_an_explicit_bundle_lists_no_parameter() -> None:
    # Holds the binding: ``BUNDLE`` spreads nothing, so a parameter listed there is
    # a value no parameter would receive.
    assert spec_to_json_schema(_spec(_plain), argument_binding=ArgumentBinding.BUNDLE) == {
        "type": "object"
    }


@_SPREADS
def test_a_many_spec_keeps_its_list_wrapper(binding: ArgumentBinding) -> None:
    # Holds ``not spec.many``: dispatch refuses a spreading binding beside ``many``,
    # and the input is the list whatever the binding names.
    spec = _spec(_plain, many=True)
    assert spec_to_json_schema(spec, argument_binding=binding) == spec_to_json_schema(spec)


_UNCHANGED: dict[str, ServiceSpec[Any, Any, Any]] = {
    "no-serializer": _spec(_plain),
    "serializer": _spec(_plain, input_serializer=_ReasonInput),
    "many": _spec(_plain, many=True, input_serializer=_ReasonInput),
    "lookup": _spec(
        _plain,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lambda *, pk: None
        ),
    ),
}
_UNCHANGED_SCHEMAS: dict[str, Any] = {
    "no-serializer": {"type": "object"},
    "serializer": _REASON_SCHEMA,
    "many": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": _REASON_SCHEMA}},
        "required": ["items"],
        "additionalProperties": False,
    },
    "lookup": {"type": "object"},
}


@pytest.mark.parametrize("name", list(_UNCHANGED))
def test_auto_is_the_schema_a_service_always_had(name: str) -> None:
    """Byte for byte, with and without the argument: ``AUTO`` resolves to ``BUNDLE``."""
    spec = _UNCHANGED[name]
    expected = json.dumps(_UNCHANGED_SCHEMAS[name])

    assert json.dumps(spec_to_json_schema(spec)) == expected
    assert json.dumps(spec_to_json_schema(spec, argument_binding=ArgumentBinding.AUTO)) == expected


def _selector(*, pk: int, note: str = "") -> None: ...


@pytest.mark.parametrize("binding", list(ArgumentBinding), ids=lambda b: b.name.lower())
def test_a_selector_spec_does_not_read_it(binding: ArgumentBinding) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_selector)
    assert spec_to_json_schema(spec, argument_binding=binding) == spec_to_json_schema(spec)


@_SPREADS
def test_the_output_phase_does_not_read_it(binding: ArgumentBinding) -> None:
    spec = _spec(_plain)
    assert spec_to_json_schema(spec, phase="output", argument_binding=binding) is None
