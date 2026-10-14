"""``kernel_spec_variant`` - a drfs spec's compiled form, as the DRF edge needs it per request."""

from __future__ import annotations

import dataclasses
import threading
import weakref
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from django_service_specs.types.selector_kind import SelectorKind

from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec
from rest_framework_services.service_specs.utils import author_values

_Variant = tuple[bool | None, bool, bool, bool, bool]

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
) -> KernelServiceSpec | KernelSelectorSpec:
    """``spec`` compiled as one request of the DRF edge dispatches it, once per variant.

    With every keyword at its default this is ``kernel_spec(spec)``, the one
    compiled spec every kernel entry point reads. Each other combination is
    compiled once per source object and kept while it lives, so a request
    never recompiles and a ``Grant`` minted for a variant covers it on the
    next call too.

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
      arguments, and a service spec's ``kwargs`` seat hands back what the edge
      registered under ``AUTHOR_VALUES_SEED`` instead.
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
    """
    key: _Variant = (partial, unguarded, target, server_input, spread)
    if key == (None, False, False, True, False):
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
                spec, partial, unguarded, target, server_input, spread
            )
    return compiled


def _compile(
    spec: Any,
    partial: bool | None,
    unguarded: bool,
    target: bool,
    server_input: bool,
    spread: bool,
) -> KernelServiceSpec | KernelSelectorSpec:
    # One arc to coverage, so each operand is named: test_partial_compiles_the_request_s_flag
    # fails without the first and test_one_spec_answers_each_binding_as_its_own
    # without the second.
    compiled = (
        as_kernel_spec(spec, partial=partial, spread=spread)
        if partial is not None or spread
        else kernel_spec(spec)
    )
    changes: dict[str, Any] = {}
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


def _handed_by_the_edge() -> Any:
    raise ImproperlyConfigured(
        "The stand-in instance lookup was asked for its row. It exists so the DRF edge "
        "can hand dispatch the row the view resolved; dispatch it with target=."
    )


_STAND_IN = KernelSelectorSpec(kind=SelectorKind.RETRIEVE, selector=_handed_by_the_edge)


__all__ = ["kernel_spec_variant"]
