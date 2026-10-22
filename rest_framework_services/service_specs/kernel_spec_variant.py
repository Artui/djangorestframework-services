"""``kernel_spec_variant`` - a drfs spec's compiled form, as the DRF edge needs it per request."""

from __future__ import annotations

import dataclasses
import sys
import threading
import weakref
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from django_service_specs.types.selector_kind import SelectorKind

from rest_framework_services.service_specs.as_kernel_spec import (
    as_kernel_spec,
    required_for_having_no_default,
)
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from rest_framework_services.service_specs.utils import author_values

_Variant = tuple[bool | None, bool, bool, bool, bool, bool, frozenset[str], frozenset[str]]

# id(source) -> (a weak reference to it, its variants by key). Keyed by identity
# for the reason ``kernel_spec`` gives: a frozen dataclass compares by its fields,
# so two equal declarations would share one variant, and a ``Grant`` binds the
# compiled spec by identity. The callback drops a source's variants with it.
_VARIANTS: dict[int, tuple[weakref.ref[Any], dict[_Variant, Any]]] = {}
_LOCK = threading.Lock()


def kernel_spec_variant(
    spec: Any,
    *,
    partial: bool | None = None,
    unguarded: bool = False,
    target: bool = False,
    server_input: bool = True,
    spread: bool = False,
    lenient: bool = False,
    excluded: frozenset[str] = frozenset(),
    seeded: frozenset[str] = frozenset(),
) -> KernelServiceSpec | KernelSelectorSpec:
    """``spec`` compiled as one request of the DRF edge dispatches it, once per variant.

    With every keyword at its default this is ``kernel_spec(spec)``, the one
    compiled spec every kernel entry point reads. Each other combination is
    compiled once per source object and kept while it lives, so a request
    never recompiles and a ``Grant`` minted for a variant covers it on the
    next call too.

    Each other combination is the edge's, and it leaves optional every read
    ``kernel_spec(spec)`` requires only for having no default: the edge fills
    such a parameter from a row a view hands in, and refuses a missing one at
    the stage it documents, the target lookup's or the service's own
    (test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for).
    ``InputRequired``, a required ``TypedDict`` key and a declared read stay
    required.

    - ``partial``: the per-request flag a ``PATCH`` sets, compiled by
      [`as_kernel_spec`][rest_framework_services.service_specs.as_kernel_spec.as_kernel_spec];
      ``None`` reads the spec's own.
    - ``unguarded``: no ``permissions``. An HTTP view has already run DRF's
      ``check_permissions``, so the edge hands ``dispatch`` a ``Grant`` and the
      compiled checks would only be asked again against a synthesized request.
    - ``target``: a service spec that declares no target lookup gets a
      stand-in instance lookup, so the edge can hand ``dispatch(target=...)``
      the row the view's ``get_object()`` resolved; the kernel refuses
      ``target=`` on a spec with no target selector. The stand-in reads no
      arguments and is never called: a dispatch reaching it raises
      ``ImproperlyConfigured``. A spec with a target lookup keeps its own.
    - ``server_input``: ``False`` compiles the spec with no ``kwargs``
      provider of its own and, on a service spec, no ``input_data``: the DRF
      edge evaluates both itself and merges what they answer into the
      arguments, and a service spec's ``kwargs`` seat hands back the author
      values the edge call carries (``author_values``) instead.
      The kernel relaxes a parameter only for a provider annotated to fill it,
      so an untyped one, as drfs has always taken, would be refused as missing
      before it ran; and a typed one declining a key it is annotated to fill
      would reach the kernel's run as a ``KeyError``. Leaving either on would
      also call the provider twice.
    - ``spread``: a service spec with no ``input_serializer`` takes the
      caller's values for its own parameters, as an explicit
      ``ArgumentBinding.SPREAD_AUTHOR_WINS`` dispatches it. Off by default, as
      ``AUTO`` and ``BUNDLE`` dispatch a service, so ``kernel_spec(spec)``
      declares none of them.
    - ``lenient``: a service spec's Validator declares its fields by name
      alone (``SerializerValidator(lenient=True)``), so the kernel's shape
      check passes each value through to the serializer. The edge compiles
      this for a request a live DRF view dispatches, which DRF's serializer
      has always answered alone: a form's ``"on"`` for a boolean, ``123`` for
      a string, every field's refusal at once, in DRF's words. A caller off
      HTTP keeps the kernel's typed check. A ``many`` spec's list is
      unbounded there too, so its ``ListSerializer`` alone bounds a live
      view's body, as DRF's view always did: a body past the kernel's
      ``SERVICE_SPECS_MAX_ARRAY_LENGTH`` is taken where the list serializer
      declares no ``max_length``, and one past a declared bound is refused in
      DRF's words.
    - ``excluded``: names a service spec's Validator declares no parameter
      for (``SerializerValidator(excluded=...)``): the edge compiles the
      names a seed of the call takes, so a serializer field of that name is
      the serializer's alone rather than a parameter the kernel refuses.
    - ``seeded``: names a seed of the call fills, which no parameter read off
      a signature takes - a selector's, a target lookup's, or a spread
      service's (``as_kernel_spec(seeded=...)``) - so the seed fills a
      parameter of that name rather than the kernel refusing it as an argument
      meeting a seed, as a service's parameter always was.
    """
    key: _Variant = (partial, unguarded, target, server_input, spread, lenient, excluded, seeded)
    if key == (None, False, False, True, False, False, frozenset(), frozenset()):
        return kernel_spec(spec)
    with _LOCK:
        held = _VARIANTS.get(id(spec))
        if held is None:
            source_id = id(spec)
            source = weakref.ref(spec, lambda _dead: _VARIANTS.pop(source_id, None))
            held = _VARIANTS[source_id] = (source, {})
        variants = held[1]
        compiled = variants.get(key)
        if compiled is None:
            compiled = variants[key] = _compile(
                spec, partial, unguarded, target, server_input, spread, lenient, excluded, seeded
            )
    return compiled


def _compile(
    spec: Any,
    partial: bool | None,
    unguarded: bool,
    target: bool,
    server_input: bool,
    spread: bool,
    lenient: bool,
    excluded: frozenset[str],
    seeded: frozenset[str],
) -> KernelServiceSpec | KernelSelectorSpec:
    # One arc to coverage, so each operand is named: test_partial_compiles_the_request_s_flag
    # fails without the first, test_one_spec_answers_each_binding_as_its_own
    # without the second, and test_both_spec_kinds_read_the_seed without the third.
    compiled = (
        as_kernel_spec(spec, partial=partial, spread=spread, seeded=seeded)
        if partial is not None or spread or seeded
        else kernel_spec(spec)
    )
    changes = _relaxed(spec, compiled)
    if unguarded:
        changes["permissions"] = None
    if not server_input:
        changes["kwargs"] = None
        # A service spec's alone: a selector spec has no ``input_data``. Its
        # ``kwargs`` seat hands back the author values the edge registered for
        # the call, which outrank what the Validator returns, as a provider's do
        # (test_a_hooks_value_outranks_the_validators_off_http).
        if isinstance(compiled, KernelServiceSpec):
            changes["input_data"] = None
            changes["kwargs"] = author_values
    # One arc to coverage, so each operand is named:
    # test_an_offline_view_keeps_the_kernels_typed_check fails without
    # ``lenient``, test_a_writable_field_named_like_a_registered_seed_validates
    # without ``excluded``, and test_a_create_body_cannot_set_a_service_parameter_over_http
    # (a spec with no input serializer, so no Validator) without the ``isinstance``.
    validator = getattr(compiled, "validator", None)
    if (lenient or excluded) and isinstance(validator, SerializerValidator):
        changes["validator"] = SerializerValidator(
            validator.serializer,
            serializer_context=validator.serializer_context,
            lenient=lenient,
            excluded=excluded,
            # Carried over, or a live view's call hands a hidden parameter the
            # client's value (test_a_client_value_for_a_hidden_parameter_stays_in_data_over_http).
            server_owned=validator.server_owned,
        )
    # The kernel reads an absent maximum as its own cap, so the largest list
    # Python can hold stands for none
    # (test_a_live_view_takes_a_body_past_the_kernels_cap), and the minimum goes
    # too, or the kernel's wording answers an empty body before DRF's
    # (test_a_list_serializers_own_bound_still_refuses_in_drfs_words, its
    # ``empty-where-it-allows-none`` cases). One arc to coverage, so each
    # conjunct is named: test_off_http_the_kernels_cap_stays fails without
    # ``lenient``, and test_json_drf_accepts_is_accepted (a spec that is not
    # ``many``, whose kernel spec refuses a list bound) without ``many``. A
    # selector spec has no ``many``.
    if lenient and getattr(compiled, "many", False):
        changes["many_min_length"] = None
        changes["many_max_length"] = sys.maxsize
    # One arc to coverage, so each conjunct is named:
    # test_a_selector_spec_takes_no_stand_in fails without the first, and
    # test_a_declared_lookup_is_kept without the second.
    if (
        target
        and isinstance(compiled, KernelServiceSpec)
        and compiled.target_selector_spec() is None
    ):
        changes["instance_selector_spec"] = _STAND_IN
    return dataclasses.replace(compiled, **changes) if changes else compiled


def _relaxed(spec: Any, compiled: Any) -> dict[str, Any]:
    """The changes that leave optional each read ``compiled`` requires for having no default.

    ``kernel_spec(spec)`` requires such a parameter, as the callable raises
    without it. The edge answers it as it always did instead: a row a view
    hands in fills the target lookup's key
    (test_a_row_handed_is_described_from_the_start,
    test_a_missing_row_under_a_list_declaration_stays_not_found,
    test_the_check_reads_the_table_not_a_prefetched_cache), and a missing one
    is refused at the stage it documents, the marked name's, the selector's or
    the lookup's (test_a_marked_name_is_asked_for_before_the_selector_s_own,
    test_a_spread_service_asks_for_its_lookup_s_key_last). Exactly those
    reads, named by ``required_for_having_no_default``, each seat held by its
    case of test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for:
    the spec's own reads by ``selector`` and ``spread-service``, the target
    lookup's by ``instance-lookup``, and the seat it sits in by
    ``collection-lookup``. A seat with no required read to relax is left as
    compiled, so a variant changing nothing else is ``kernel_spec(spec)``
    itself (test_a_variant_with_nothing_to_relax_is_the_compiled_spec: its
    ``already-optional`` case holds the ``required`` check).
    """
    own, lookup = required_for_having_no_default(spec)
    changes: dict[str, Any] = {}
    reads = _optional(compiled.reads, own)
    if reads is not None:
        changes["reads"] = reads
    # A selector spec has no target lookup, and ``required_for_having_no_default``
    # names none for it.
    target = compiled.target_selector_spec() if lookup else None
    # One arc to coverage: every selector spec's variant fails without the
    # first conjunct, and the ``lookup-already-optional`` case of
    # test_a_variant_with_nothing_to_relax_is_the_compiled_spec without the second.
    if target is not None and (relaxed := _optional(target.reads, lookup)) is not None:
        seat = (
            "instance_selector_spec"
            if compiled.instance_selector_spec is target
            else "collection_selector_spec"
        )
        changes[seat] = dataclasses.replace(target, reads=relaxed)
    return changes


def _optional(reads: Parameters, names: frozenset[str]) -> Parameters | None:
    """``reads`` with each required one ``names`` names made optional, or ``None`` where none is."""
    if not any(parameter.required and parameter.name in names for parameter in reads):
        return None
    return Parameters(
        tuple(
            dataclasses.replace(parameter, required=False) if parameter.name in names else parameter
            for parameter in reads
        )
    )


def _handed_by_the_edge() -> Any:
    raise ImproperlyConfigured(
        "The stand-in instance lookup was asked for its row. It exists so the DRF edge "
        "can hand dispatch the row the view resolved; dispatch it with target=."
    )


_STAND_IN = KernelSelectorSpec(kind=SelectorKind.RETRIEVE, selector=_handed_by_the_edge)


__all__ = ["kernel_spec_variant"]
