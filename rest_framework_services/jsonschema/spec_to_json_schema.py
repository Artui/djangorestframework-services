"""``spec_to_json_schema`` — derive a JSON Schema straight from a spec."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.utils import callable_input_schema
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Pool seeds the transport injects rather than the caller supplying them, so
# they are skipped when reflecting a selector's parameters. A param filled by a
# ``spec.kwargs`` provider can't be skipped statically (a callable, not a known
# key set) and is surfaced anyway — harmless, since every reflected property is
# optional.
_SELECTOR_SEED_PARAMS: frozenset[str] = frozenset({"request", "user", "view"})

# The one ``metadata`` key this package reads, and the only two keys allowed
# under it. They are spelled exactly like the ``phase=`` argument below so a
# reader has nothing new to learn.
_JSON_SCHEMA_METADATA_KEY = "json_schema"
_SCHEMA_PHASES: tuple[str, ...] = ("input", "output")


def spec_to_json_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    phase: Literal["input", "output"] = "input",
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    """Derive a JSON Schema from a spec, reading the right serializer off it.

    The convenience an alternate transport (a Pydantic-AI toolset, the MCP server) calls
    instead of reaching into spec internals itself. ``registry`` supplies consumer rules
    for custom field / filter / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``phase="input"`` (default) returns the input-argument schema:

    - [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] → its
        ``input_serializer`` (``spec.partial`` honoured).
    - [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] → an
      object whose ``properties`` combine the selector callable's own annotated
      parameters (skipping the ``request`` / ``user`` / ``view`` transport seeds) with
      its ``filter_set`` fields, so ``get_widget(user, pk)`` advertises ``pk`` instead
      of leaning on its docstring; a bare ``{"type": "object"}`` when it exposes
      neither. A ``**kwargs: Unpack[SomeExtras]`` parameter is **expanded** into one
      property per ``TypedDict`` key, its required keys populating ``required``, so a
      URL kwarg read from ``extras`` is discoverable off-HTTP rather than a hidden
      ``KeyError``. Introspecting a ``filter_set`` needs the ``[filter]`` extra.

    ``phase="output"`` returns the output schema, or ``None`` when undeclared: a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies its
    ``output_selector_spec``'s ``output_serializer`` and ``kind``, a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] its own.

    ``max_depth`` bounds how many serializer levels are described, truncating
    deeper ones to ``{"type": "object"}``; ``None``, the default, describes them
    all. It reaches the serializer-backed schemas — a ``ServiceSpec``'s input
    and either spec's output — and has nothing to bound on a ``SelectorSpec``'s
    input, which is reflected from a callable and a ``filter_set`` rather than
    walked. A serializer that nests itself is truncated after a fixed number of
    appearances regardless, because the alternative is a ``RecursionError``
    raised while a transport declares its tools; where the two disagree the
    tighter wins, so this still yields exactly the levels it names.

    **``metadata["json_schema"]`` is the one declaration this merges on top.**
    Derivation reads serializers and callables, so there is nowhere for it to
    find a `title` for the operation or a sentence saying what the operation
    does; every transport was left to invent its own, from the spec name or a
    docstring. A consumer writes the fragment once, on the spec:

        ServiceSpec(
            service=archive_project,
            input_serializer=ArchiveInput,
            metadata={
                "json_schema": {
                    "input": {"title": "Archive project", "description": "Retire a project."}
                }
            },
        )

    It is **keyed by phase**, with the same two words ``phase=`` takes. One flat
    fragment merged into both would hang the operation's description off the
    output schema, which describes what comes back rather than what to send —
    two different sentences that only ever coincide by accident. A key that is
    neither ``"input"`` nor ``"output"`` raises rather than being ignored,
    because omitting the phase key is the mistake this shape invites and
    silently publishing nothing is the worst way to report it.

    **The fragment wins, key by key, and the merge is shallow.** It is an
    author's explicit declaration standing against a *derived* value, so a
    derivation it could not override would leave a wrong derivation unfixable —
    which is the whole reason the hatch exists. Shallow means one rule: a key
    the fragment names is the fragment's, whole. So a fragment naming
    ``properties`` replaces the entire derived block rather than adding to it,
    which is the sharp edge and is deliberate — the alternative is a per-key
    policy for ``properties`` and another for ``required``, and every answer
    there is wrong for somebody.

    A fragment **annotates a derived schema and never conjures one**: where
    ``phase="output"`` yields ``None`` because nothing declares an output, an
    ``"output"`` fragment leaves it ``None``. Otherwise ``metadata`` would
    become a schema-authoring channel and a fragment carrying only a
    ``description`` would publish as an output schema describing nothing.

    The fragment is read off **the spec passed in**, never off a nested one:
    ``metadata`` does not merge or inherit, so a ``ServiceSpec``'s output schema
    takes the ``ServiceSpec``'s fragment even though the serializer behind it
    came from ``output_selector_spec``.

    Validation happens here rather than at construction. ``metadata`` is
    declared by consumers who may never generate a schema, and checking a
    reserved key on every ``ServiceSpec(...)`` would mean the kernel reads
    metadata contents — the one thing the field promises it does not do.
    """
    fragment: Mapping[str, Any] | None = _metadata_fragment(spec, phase)
    derived: dict[str, Any] | None = (
        _input_schema(spec, registry, max_depth)
        if phase == "input"
        else _output_schema(spec, registry, max_depth)
    )
    if derived is None or fragment is None:
        return derived
    return {**derived, **fragment}


def _metadata_fragment(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], phase: str
) -> Mapping[str, Any] | None:
    """The ``metadata["json_schema"][phase]`` fragment, or ``None`` if undeclared.

    Read before the schema is derived so a malformed declaration is reported
    even on a phase that derives nothing.
    """
    metadata: Mapping[str, Any] | None = spec.metadata
    if metadata is None:
        return None
    declared: Any = metadata.get(_JSON_SCHEMA_METADATA_KEY)
    if declared is None:
        return None
    label: str = f"{type(spec).__name__}.metadata[{_JSON_SCHEMA_METADATA_KEY!r}]"
    if not isinstance(declared, Mapping):
        raise ImproperlyConfigured(
            f"{label} must be a mapping keyed by schema phase "
            f"({' / '.join(repr(name) for name in _SCHEMA_PHASES)}); got "
            f"{type(declared).__name__}."
        )
    unknown: list[str] = sorted(str(key) for key in declared if key not in _SCHEMA_PHASES)
    if unknown:
        raise ImproperlyConfigured(
            f"{label} declares {', '.join(repr(name) for name in unknown)}, which name no "
            f"schema phase. Nest the fragment under "
            f"{' or '.join(repr(name) for name in _SCHEMA_PHASES)} — an input schema and an "
            "output schema describe different things and cannot share one title or one "
            "description."
        )
    fragment: Any = declared.get(phase)
    if fragment is None:
        return None
    if not isinstance(fragment, Mapping):
        raise ImproperlyConfigured(
            f"{label}[{phase!r}] must be a mapping of JSON Schema keys to merge; got "
            f"{type(fragment).__name__}."
        )
    return fragment


def _input_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    registry: JsonSchemaRegistry,
    max_depth: int | None,
) -> dict[str, Any]:
    if isinstance(spec, ServiceSpec):
        return serializer_to_json_schema(
            spec.input_serializer,
            partial=bool(spec.partial),
            registry=registry,
            max_depth=max_depth,
        )
    schema: dict[str, Any] = {"type": "object"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    if spec.selector is not None:
        callable_props, callable_required = callable_input_schema(
            spec.selector, skip=_SELECTOR_SEED_PARAMS, registry=registry
        )
        properties.update(callable_props)
        required.extend(callable_required)
    if spec.filter_set is not None:
        # A declared filter_set field is the more precise source for a shared
        # name, so it wins over a bare callable parameter of the same name.
        properties.update(filterset_to_json_schema(spec.filter_set, registry=registry))
    if properties:
        schema["properties"] = properties
    if required:
        # Only ``Unpack[TypedDict]`` extras contribute requiredness; dedupe
        # defensively.
        schema["required"] = list(dict.fromkeys(required))
    return schema


def _output_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    registry: JsonSchemaRegistry,
    max_depth: int | None,
) -> dict[str, Any] | None:
    if isinstance(spec, ServiceSpec):
        nested = spec.output_selector_spec
        if nested is None:
            return None
        return output_to_json_schema(
            nested.output_serializer, kind=nested.kind, registry=registry, max_depth=max_depth
        )
    return output_to_json_schema(
        spec.output_serializer, kind=spec.kind, registry=registry, max_depth=max_depth
    )
