"""``spec_to_json_schema`` — derive a JSON Schema straight from a spec."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.can_present_nothing import can_present_nothing
from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.utils import (
    callable_input_schema,
    list_constraints_for_schema,
)
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Pool seeds the transport injects rather than the caller supplying them, so
# they are skipped when reflecting a selector's parameters. A param filled by a
# ``spec.kwargs`` provider can't be skipped from here (a callable, not a known
# key set); a transport that knows which names it fills says so with
# ``supplied=``, and without that statement no parameter is inferred required.
# Under ``supplied=`` the reserved pool seeds are dropped too, joined to the
# statement rather than listed here, so the default reflection is unchanged.
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
    supplied: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """Derive a JSON Schema from a spec, reading the right serializer off it.

    The convenience an alternate transport (a Pydantic-AI toolset, the MCP server) calls
    instead of reaching into spec internals itself. ``registry`` supplies consumer rules
    for custom field / filter / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``phase="input"`` (default) returns the input-argument schema:

    - [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] → its
        ``input_serializer`` (``spec.partial`` honoured). On a ``many=True`` spec that
        schema describes one item, and the input is an object with a single required
        property named by ``spec.many_argument`` -- ``{"type": "array", "items":
        <item>}``, with ``minItems`` / ``maxItems`` where the list serializer declares
        ``allow_empty=False`` / ``min_length`` / ``max_length`` -- and
        ``additionalProperties: false``. A transport describing a tool passes
        arguments as an object, which can never be the bare array the HTTP body is;
        dispatch that input with ``many_as_argument=True``. ``partial`` relaxes the
        item and never the argument, which DRF requires under ``partial`` too, and
        ``max_depth`` counts from the item, since the wrapper is not a serializer.
    - [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] → an
      object whose ``properties`` combine the selector callable's own annotated
      parameters (skipping the ``request`` / ``user`` / ``view`` transport seeds) with
      its ``filter_set`` fields, so ``get_widget(user, pk)`` advertises ``pk`` instead
      of leaning on its docstring; a bare ``{"type": "object"}`` when it exposes
      neither. A ``**kwargs: Unpack[SomeExtras]`` parameter is **expanded** into one
      property per ``TypedDict`` key, its required keys populating ``required``, so a
      URL kwarg read from ``extras`` is discoverable off-HTTP rather than a hidden
      ``KeyError``. Introspecting a ``filter_set`` needs the ``[filter]`` extra.

    ``supplied`` is for a transport describing its own tools, and names what that
    transport fills itself beyond the seeds every transport reserves: its
    registered pool seeds, the names a ``kwargs=`` provider returns, URL kwargs.
    ``None``, the default, leaves the reflection exactly as it was without it:
    every reflected parameter is optional unless ``InputRequired`` (or a required
    ``TypedDict`` key) says otherwise, because a reader that does not know what the
    transport fills cannot tell a caller input from a pool value. A frozenset is
    that knowledge, and applies it to the selector callable's reflected parameters:

    - a name in ``supplied`` is dropped from ``properties`` and ``required``, marked
      or not -- the transport fills it, and the client cannot replace it;
    - so is every name in
      [`RESERVED_POOL_SEEDS`][rest_framework_services.types.reserved_pool_seeds]
      (``progress``, ``data`` and the rest), without being listed, because client
      input can never take one: the pool's ``request``, ``user`` and ``progress``
      are filled over whatever a caller sends, and a selector's params never
      reach the others;
    - any other parameter with no default that can be passed by keyword joins
      ``required``: nothing but the caller's input is left to fill it, and the call
      raises ``TypeError`` without it -- so ``task_by_pk(user, *, pk)`` advertises
      ``pk`` as required rather than as an option. A positional-only one is
      advertised and never required, because dispatch passes everything by
      keyword and no input can fill it;
    - a parameter with a default stays optional, ``InputRequired`` stays required,
      and ``NotClientInput`` and the ``request`` / ``user`` / ``view`` seeds stay
      unadvertised;
    - an expanded ``TypedDict`` key has no default to read, so a supplied or
      reserved one is dropped and any other keeps the requiredness its
      ``TypedDict`` declares.

    It reaches nothing else. A ``ServiceSpec``'s input is its ``input_serializer``,
    whose fields are not reflected parameters, and ``phase="output"`` reflects no
    parameters at all; both are the same with or without ``supplied``. So is a
    ``filter_set`` field, which the filter reads from the caller's input. A
    transport reflecting a nested ``instance_selector_spec`` or
    ``collection_selector_spec`` passes that ``SelectorSpec`` itself, with the names
    it fills there.

    ``phase="output"`` returns the output schema, or ``None`` when undeclared: a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies its
    ``output_selector_spec``'s ``output_serializer`` and ``affordances``, a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] its own.
    A ``ServiceSpec``'s schema states the kind dispatch renders, which is not always
    the one declared: an array for ``many=True``, the nested ``kind`` where the
    ``output_selector_spec`` has a ``selector`` to re-read through, and one value
    otherwise, because with nothing to re-read dispatch presents the service's own
    return whatever ``kind`` the nested spec names. Declared ``affordances`` add the ``affordances`` object each rendered item
    carries, ``reason`` included -- the shape ``render_spec_output`` produces.
    Where dispatch may present ``None``, the item's type is ``["object", "null"]``,
    as
    [`can_present_nothing`][rest_framework_services.can_present_nothing.can_present_nothing]
    answers it: an ``allow_none`` RETRIEVE ``SelectorSpec``'s miss, a single-row
    ``ServiceSpec`` whose ``output_selector_spec`` re-reads through a ``selector``
    that may find no row, and a ``ServiceSpec`` declaring ``allow_none=True``. A
    ``ServiceSpec``'s nested ``output_selector_spec.allow_none`` is not read, as
    dispatch does not read it.

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
        _input_schema(spec, registry, max_depth, supplied)
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
    supplied: frozenset[str] | None,
) -> dict[str, Any]:
    if isinstance(spec, ServiceSpec):
        item: dict[str, Any] = serializer_to_json_schema(
            spec.input_serializer,
            partial=bool(spec.partial),
            registry=registry,
            max_depth=max_depth,
        )
        if not spec.many:
            return item
        return {
            "type": "object",
            "properties": {
                spec.many_argument: {
                    "type": "array",
                    "items": item,
                    **list_constraints_for_schema(spec.input_serializer),
                }
            },
            "required": [spec.many_argument],
            # The list is the whole input: dispatch refuses any argument beside it,
            # so the schema says so rather than inviting one.
            "additionalProperties": False,
        }
    schema: dict[str, Any] = {"type": "object"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    if spec.selector is not None:
        callable_props, callable_required = callable_input_schema(
            spec.selector,
            skip=_SELECTOR_SEED_PARAMS,
            registry=registry,
            # The reserved seeds join a transport's statement rather than
            # ``skip``: client input can never take one (the pool's ``progress``
            # is filled over whatever a caller sends, and a selector's params
            # never reach the rest), so a transport need not list them, while
            # ``None`` stays the reflection it always was. Held by
            # test_reserved_pool_seeds_are_dropped_without_being_supplied and
            # test_reserved_pool_seeds_reflect_as_before_without_supplied.
            supplied=None if supplied is None else supplied | RESERVED_POOL_SEEDS,
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
        # Markers, required ``TypedDict`` keys and, under ``supplied``, a
        # parameter without a default contribute requiredness; dedupe
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
            nested.output_serializer,
            kind=_rendered_kind(spec, nested),
            registry=registry,
            max_depth=max_depth,
            affordances=nested.affordances,
            allow_none=can_present_nothing(spec),
        )
    return output_to_json_schema(
        spec.output_serializer,
        kind=spec.kind,
        registry=registry,
        max_depth=max_depth,
        affordances=spec.affordances,
        # The same question as the branch above, asked of the same function, so the
        # schema and every transport calling it answer it alike.
        allow_none=can_present_nothing(spec),
    )


def _rendered_kind(
    spec: ServiceSpec[Any, Any, Any], nested: SelectorSpec[Any, Any]
) -> SelectorKind:
    """The kind a service spec's result is rendered as, which its output schema states.

    Dispatch decides it and reports it as ``result.kind``, which the HTTP view and
    every transport render from, so the schema follows dispatch rather than the
    declaration:

    - A ``many=True`` service renders the whole list. Its output selector is
      ``RETRIEVE`` by convention, because that kind describes one row.
    - An output selector with a ``selector`` re-reads the result, and its ``kind``
      says whether the re-read is one row or a set.
    - Without a ``selector`` nothing is re-read: dispatch presents the service's
      own return as one value, whatever ``kind`` the declaration names.
    """
    if spec.many:
        return SelectorKind.LIST
    if nested.selector is None:
        # Held by test_a_list_output_declaration_without_a_selector_is_one_value,
        # and by the manifest's and the predicate table's rows for the same spec:
        # without this, a ``LIST`` declaration with nothing to re-read through
        # publishes an array for the single value dispatch serves.
        return SelectorKind.RETRIEVE
    return nested.kind
