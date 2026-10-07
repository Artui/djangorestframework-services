"""``provider_keys`` -- the keys a ``kwargs=`` provider declares, read before it runs."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Mapping
from types import SimpleNamespace, UnionType
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin

from typing_extensions import Format, get_annotations, get_type_hints

from rest_framework_services.types.provider_keys import ProviderKeys
from rest_framework_services.types.unset import UnsetType

# The two runtime spellings of a union, ``Union[a, b]`` and ``a | b``: separate
# origins up to Python 3.13, one object from 3.14. A union's arguments are the
# alternatives to the value, so whether one of them is ``UnsetType`` is whether
# the value may be ``UNSET``.
_UNIONS: tuple[Any, ...] = (Union, UnionType)


def provider_keys(
    provider: Callable[..., Any] | None,
) -> ProviderKeys | None:
    """The names ``provider`` fills and those it may decline, or ``None`` if it does not say.

    The static half of the ``kwargs=`` provider contract, whose runtime half is
    how dispatch calls the provider. ``ServiceSpec.kwargs`` and
    ``SelectorSpec.kwargs`` are typed ``Callable[..., ExtraT]``, with ``ExtraT``
    documented as a ``TypedDict`` of the keys the provider returns, so a
    provider annotated that way says, before it runs, which names it fills. A
    transport reads that here to build a schema that does not ask the caller
    for them, and to know which calls to refuse before dispatch, rather than
    keeping a copy of this reader.

    Returns a
    [`ProviderKeys`][rest_framework_services.types.provider_keys.ProviderKeys],
    ``(filled, declinable)``: two disjoint sets that together are every key of
    the ``TypedDict``, the keys the provider always answers for and the keys
    whose value admits ``UnsetType``, which it may decline with ``UNSET``. It
    unpacks as a pair and names each set, so ``keys.filled`` reads as well as
    ``filled, declinable = keys``.

    ``ProviderKeys(frozenset(), frozenset())`` for ``provider=None``: a spec with
    no provider fills nothing. ``None`` when the provider says nothing about its
    keys -- no return annotation, a plain ``dict``, a lambda, or a return
    annotation that does not resolve -- so it may fill any name, and a transport
    requires none of them for lacking a default. Held by
    ``test_a_spec_without_a_provider_fills_nothing`` and
    ``test_a_provider_whose_return_says_nothing_is_untyped``.

    **Each annotation costs only what it says.** The return annotation is
    resolved on its own, in the globals of the function a decorator wrapped
    rather than the wrapper's (``decorated-elsewhere`` in
    ``test_a_generic_typed_dicts_arguments_decide_which_keys_may_be_declined``),
    so a parameter annotation naming a type imported only under
    ``TYPE_CHECKING`` leaves the keys readable
    (``test_a_parameter_type_imported_only_for_type_checking_leaves_the_keys_readable``).
    The keys are read off the ``TypedDict`` without hints, and each value is
    resolved on its own, so one that does not resolve makes only its own key
    declinable: whether the provider may decline it cannot be read, and
    declinable is the reading that neither hides the name from the caller nor
    requires it (``test_a_value_type_that_does_not_resolve_makes_only_its_key_declinable``).
    Python 3.14's lazily evaluated annotations are read the same way, in the
    format that leaves an unresolvable name as a forward reference rather than
    raising (``test_lazily_evaluated_annotations_are_read_one_at_a_time``).
    That format is why ``typing-extensions`` is floored at 4.14: on 3.14, 4.13
    hands the standard library's ``annotationlib`` a format number it reserves
    for internal use, which raises, and every typed provider would read as
    untyped.

    **A generic ``TypedDict`` is read with whatever binds its parameters.**
    ``Scope[str | UnsetType]`` does not relay ``__required_keys__``, so the keys
    come off ``Scope``, where a value annotated ``T`` is the bare type variable.
    What ``T`` stands for is substituted wherever it appears in a value, a union
    included, whether an alias's arguments bind it
    (``test_a_generic_typed_dicts_arguments_decide_which_keys_may_be_declined``)
    or a class statement does, as ``class Declining(Scope[str | UnsetType])``
    does, and a subclass handing its own parameter on to ``Scope``
    (``test_a_subclass_binding_decides_which_keys_may_be_declined``). An
    argument that does not admit ``UNSET`` still fills the key
    (``test_a_parameterised_typed_dict_is_read_off_its_origin``), and one
    wrapped in ``Annotated`` is read as what it wraps
    (``test_annotated_around_a_binding_is_read_as_what_it_annotates``). A PEP 695
    ``class Scope[T](TypedDict)`` is read the same way under postponed
    annotations, where the ``"T"`` a value leaves resolves only beside the
    class's ``__type_params__``
    (``test_a_pep_695_type_parameter_is_read_as_the_class_declares_it``).

    **Each key is read with the bindings of the class that declared it**, so
    one ``T`` reused at every level reads as each class binds it:
    ``class Shadow(Scope[list[T]], Generic[T])`` with ``extra: T``, returned
    as ``Shadow[str | UnsetType]``, fills ``tenant`` with a list and may
    decline ``extra``
    (``test_each_key_is_read_with_the_bindings_of_the_class_that_declared_it``).
    A key reached through two bases that bind it differently is filled if
    either reading fills it.

    **A type variable nothing binds counts as filled**, as in a bare
    ``-> Scope`` (``test_a_type_variable_nothing_binds_is_filled``). Whether it
    may be ``UNSET`` is written nowhere, and filled keeps the decision with the
    provider by hiding the key from the caller. Declinable would let a
    transport whose caller's value wins put that value in a key the provider
    was meant to decide, such as a tenant.

    Three readings stay as they are, each held by a test:

    - A PEP 696 default on a type variable is not consulted
      (``test_a_type_variables_default_is_not_consulted``).
    - A base that cannot be substituted makes the provider read as untyped
      (``test_a_base_that_cannot_be_substituted_leaves_the_provider_untyped``).
    - A key a subclass redeclares is read with its base's bindings, since no
      runtime record says which class wrote it
      (``test_a_key_a_subclass_redeclares_is_read_with_its_bases_bindings``).

    Duck-typed on the keys a ``TypedDict`` class carries rather than
    ``is_typeddict``, because the standard library's answers ``False`` for a
    ``typing_extensions.TypedDict`` on the older Pythons this package supports.
    """
    if provider is None:
        return ProviderKeys(frozenset(), frozenset())
    try:
        returned = _resolve(
            get_annotations(provider, format=Format.FORWARDREF).get("return"),
            getattr(inspect.unwrap(provider), "__globals__", {}),
        )
        declared: Any = get_origin(returned) or returned
        if getattr(declared, "__required_keys__", None) is None:
            return None
        values = get_annotations(declared, format=Format.FORWARDREF)
        declarations = _declarations(returned)
    except Exception:
        # A return annotation that does not resolve, a callable no annotations
        # can be read off, or a generic whose bases cannot be substituted
        # through: either way nothing usable was declared.
        return None
    namespace = getattr(sys.modules.get(declared.__module__), "__dict__", {})
    readings = [
        (name, _may_decline(values.get(name), namespace, type_parameters, bindings))
        for keys, bindings, type_parameters in declarations
        for name in keys
    ]
    # A key reached through two bases that bind it differently is read once
    # through each, and filled by either reading is filled
    # (``two-bindings-one-key`` holds the subtraction).
    filled = frozenset(name for name, declines in readings if not declines)
    declinable = frozenset(name for name, declines in readings if declines) - filled
    return ProviderKeys(_keys(declared) - declinable, declinable)


def _keys(declared: Any) -> frozenset[str]:
    """Every key ``declared`` carries, its bases' included; none if it is not a ``TypedDict``."""
    return frozenset(getattr(declared, "__required_keys__", ())) | frozenset(
        getattr(declared, "__optional_keys__", ())
    )


def _may_decline(
    annotation: Any,
    namespace: dict[str, Any],
    type_parameters: dict[str, Any],
    bindings: Mapping[Any, Any],
) -> bool:
    """Whether a key annotated ``annotation`` may come back ``UNSET``; ``True`` if unreadable."""
    try:
        value = _resolve(annotation, namespace, type_parameters)
    except Exception:
        # Most often a name imported only under ``TYPE_CHECKING``. Which way the
        # key reads cannot be known, so it reads the way that asks least.
        return True
    return _admits_unset(value, bindings)


def _declarations(
    returned: Any,
) -> list[tuple[frozenset[str], dict[Any, Any], dict[str, Any]]]:
    """Each class ``returned`` is built from: the keys it declares, its bindings, and its PEP 695 parameters.

    ``returned`` is a ``TypedDict`` or an alias of one. The alias's arguments
    bind its origin's parameters, and each base in ``__orig_bases__`` binds its
    own origin's in turn, so ``class Declining(Scope[str | UnsetType])`` binds
    ``Scope``'s ``T`` with no alias in sight. A base's arguments are written in
    the terms of the class below it, so they are substituted with that class's
    bindings: ``-> Relay[str | UnsetType]``, with ``Relay(Scope[U], Generic[U])``,
    binds ``U`` and then ``T`` to ``str | UnsetType``. Every binding is then in
    the terms of the return annotation, which is why ``_admits_unset`` walks
    one without substituting again. A parameter nothing binds is bound to
    itself, and stays free.

    Bindings are kept per class, because one type variable may be bound
    differently at each level, as ``class Shadow(Scope[list[T]], Generic[T])``
    binds ``Scope``'s ``T`` to a list and leaves its own to the alias. A
    ``TypedDict``'s hints and its key sets are merged from every base on every
    supported Python, ``typing``'s and ``typing_extensions``' alike, so the
    keys a class declares are its keys less every base's, and each is read
    with the bindings of that class and resolved beside its PEP 695
    parameters, which a postponed annotation's ``"T"`` names because they live
    in ``__type_params__`` rather than in the module. A key redeclared in a
    subclass is the base's by that subtraction. The merged hints carry the
    subclass's annotation and no record of which class wrote it, and an
    eagerly evaluated ``tenant: T`` written at both levels is one object in
    both, so a redeclared key cannot be told from an inherited one.

    Each step is held by a case of
    ``test_a_subclass_binding_decides_which_keys_may_be_declined``:
    ``through-a-class-base`` holds walking a base that is a class rather than
    an alias, ``relays-a-declinable-type`` substituting a base's arguments,
    and ``bare-rebinding`` a parameter bound to itself when the alias gives no
    arguments. ``test_each_key_is_read_with_the_bindings_of_the_class_that_declared_it``
    holds keeping them per class (``shadow``) and subtracting the bases' keys
    (``reverse``, which reads every key at every level without it).
    ``test_a_pep_695_type_parameter_is_read_as_the_class_declares_it`` holds the
    parameters: ``subclass`` that they are a base's when a base declares the
    key, and ``same-name-rebound`` and ``own-key`` that they are the declaring
    class's alone, in each direction.
    """
    declarations: list[tuple[frozenset[str], dict[Any, Any], dict[str, Any]]] = []
    pending = [(get_origin(returned) or returned, get_args(returned))]
    while pending:
        declared, arguments = pending.pop()
        parameters = getattr(declared, "__parameters__", ())
        # Not ``strict``: a bare ``-> Scope`` gives no arguments.
        bindings = dict(zip(parameters, arguments or parameters, strict=False))
        bases = [
            (get_origin(base) or base, tuple(_substitute(a, bindings) for a in get_args(base)))
            for base in getattr(declared, "__orig_bases__", ())
        ]
        inherited = {name for base, _ in bases for name in _keys(base)}
        type_parameters = {p.__name__: p for p in getattr(declared, "__type_params__", ())}
        declarations.append((_keys(declared) - inherited, bindings, type_parameters))
        pending.extend(bases)
    return declarations


def _substitute(argument: Any, bindings: Mapping[Any, Any]) -> Any:
    """``argument`` with each type variable in it replaced by what ``bindings`` binds it to.

    Through the argument's own substitution, the way ``typing`` substitutes an
    alias, so ``U | None`` with ``U`` bound to ``str | UnsetType`` is
    ``str | UnsetType | None``. ``relays-inside-a-union`` holds that; a type
    variable alone has no parameters to substitute through, and
    ``relays-a-declinable-type`` holds that it is replaced all the same.
    """
    if isinstance(argument, TypeVar):
        return bindings[argument]
    parameters = getattr(argument, "__parameters__", ())
    return argument[tuple(bindings[p] for p in parameters)] if parameters else argument


def _resolve(
    annotation: Any, namespace: dict[str, Any], local: dict[str, Any] | None = None
) -> Any:
    """``annotation`` evaluated in ``namespace`` the way ``get_type_hints`` evaluates one.

    ``get_type_hints`` is the evaluator that resolves every form an annotation
    arrives in here: a string under postponed annotations, a forward reference,
    and an evaluated object with a forward reference inside it, as Python 3.14's
    lazily evaluated annotations return ``list[Hidden]``. It resolves all of an
    object's annotations at once, so it is handed a holder carrying only this
    one. ``typing_extensions``' version strips ``Annotated``, ``Required``,
    ``NotRequired`` and ``ReadOnly`` on every supported Python, where the
    standard library's left ``typing_extensions.NotRequired`` in place on 3.10,
    so ``_admits_unset`` meets only what the wrappers wrap. The wrapper cases of
    ``test_a_key_whose_value_admits_unset_may_be_declined`` hold that on 3.10,
    where the standard library's hints would leave three of the four in place.

    ``local`` carries the PEP 695 parameters a value may name, kept apart from
    ``namespace`` rather than merged into it. Up to 3.13 a forward reference
    answers with the value it last resolved to whenever it is evaluated again
    with one namespace standing for both, and the ``"T"`` in a ``TypedDict``'s
    hints is one object shared with every subclass, so another reader
    resolving a subclass's hints first would decide the answer here
    (``test_a_type_parameter_resolved_elsewhere_first_does_not_decide_the_answer``).
    ``namespace`` is the module that defined the ``TypedDict``. Only a forward
    reference carrying no module of its own reads it, because the class and
    functional syntaxes stamp their module on each one they build
    (``test_a_forward_reference_without_a_module_resolves_where_the_typed_dict_was_defined``).
    """
    holder = SimpleNamespace(__annotations__={"value": annotation})
    return get_type_hints(holder, globalns=namespace, localns=local)["value"]


def _admits_unset(annotation: Any, bindings: Mapping[Any, Any]) -> bool:
    """Whether a value annotated ``annotation`` may be drf-services' ``UNSET``.

    ``UnsetType`` itself, or a union with it among its alternatives, at any
    depth: ``str | UnsetType``, ``Union[str, UnsetType]``,
    ``Optional[Union[str, UnsetType]]``, and any of those inside a wrapper,
    which ``_resolve`` has already stripped. Any other generic stops the walk,
    because its arguments are not the value: ``list[str | UnsetType]`` is a key
    always filled with a list, and dispatch drops a key only when its value *is*
    ``UNSET``. A type variable, at any level, is walked as what the bindings
    of the class that declared the key say it stands for, and that is walked
    in turn rather than compared with
    ``UnsetType`` (``union-argument-inside-a-union`` in
    ``test_a_generic_typed_dicts_arguments_decide_which_keys_may_be_declined``).
    It is walked with no bindings, because ``_declarations`` has already written it
    in the return annotation's terms: ``bare-rebinding`` in
    ``test_a_subclass_binding_decides_which_keys_may_be_declined`` binds ``T``
    to ``T | UnsetType``, which substituting again would expand without end.
    One nothing binds is filled, and asking whether it is bound before looking
    it up is held by ``test_a_type_variable_nothing_binds_is_filled``, the
    variable met again inside a binding being one nothing binds.

    ``Annotated`` is walked as what it annotates, at any depth the walk
    reaches, the way ``_resolve`` strips it from a key's own value. Only a
    binding can still carry one, since a class statement's bases are kept in
    ``__orig_bases__`` as written and never resolved
    (``test_annotated_around_a_binding_is_read_as_what_it_annotates``, where
    ``inside-a-union`` meets it below a union and ``relayed`` around a type
    variable a subclass hands on).

    One arc to coverage, so each condition is named on the test that holds it.
    ``test_a_key_whose_value_admits_unset_may_be_declined`` holds the walk,
    since a union reaches ``UnsetType`` only through it, and each member of
    ``_UNIONS`` by its spelling: ``[typing-union]`` holds ``Union`` and
    ``[pep-604-union]`` holds ``UnionType``, on Python 3.10 to 3.13, where the
    two are separate origins.
    ``test_a_union_that_does_not_admit_unset_is_still_filled`` holds that having
    arguments is not enough, and
    ``test_a_container_of_unset_is_not_a_key_the_provider_may_decline`` that only
    a union's arguments are walked.
    """
    if isinstance(annotation, TypeVar):
        return annotation in bindings and _admits_unset(bindings[annotation], {})
    if get_origin(annotation) is Annotated:
        return _admits_unset(get_args(annotation)[0], bindings)
    return annotation is UnsetType or (
        get_origin(annotation) in _UNIONS
        and any(_admits_unset(argument, bindings) for argument in get_args(annotation))
    )


__all__ = ["provider_keys"]
