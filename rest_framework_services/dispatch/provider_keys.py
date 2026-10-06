"""``provider_keys`` -- the keys a ``kwargs=`` provider declares, read before it runs."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Mapping
from types import SimpleNamespace, UnionType
from typing import Any, TypeVar, Union, get_args, get_origin

from typing_extensions import Format, get_annotations, get_type_hints

from rest_framework_services.types.unset import UnsetType

# The two runtime spellings of a union, ``Union[a, b]`` and ``a | b``: separate
# origins up to Python 3.13, one object from 3.14. A union's arguments are the
# alternatives to the value, so whether one of them is ``UnsetType`` is whether
# the value may be ``UNSET``.
_UNIONS: tuple[Any, ...] = (Union, UnionType)


def provider_keys(
    provider: Callable[..., Any] | None,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """The names ``provider`` fills and those it may decline, or ``None`` if it does not say.

    The static half of the ``kwargs=`` provider contract, whose runtime half is
    how dispatch calls the provider. ``ServiceSpec.kwargs`` and
    ``SelectorSpec.kwargs`` are typed ``Callable[..., ExtraT]``, with ``ExtraT``
    documented as a ``TypedDict`` of the keys the provider returns, so a
    provider annotated that way says, before it runs, which names it fills. A
    transport reads that here to build a schema that does not ask the caller
    for them, and to know which calls to refuse before dispatch, rather than
    keeping a copy of this reader.

    Returns ``(filled, declinable)``, two disjoint sets that together are every
    key of the ``TypedDict``:

    - ``filled``: the keys the provider always answers for, ``NotRequired`` ones
      included, since the provider owns them, so a transport need not ask the
      caller for them.
    - ``declinable``: the keys whose value admits ``UnsetType``
      (``tenant: str | UnsetType``). The provider may return ``UNSET`` for one,
      which dispatch removes from the pool, so the caller's value is the one
      the callable receives. Such a key is the caller's to send, but not
      required of it, since the provider may fill it after all.

    ``(frozenset(), frozenset())`` for ``provider=None``: a spec with no
    provider fills nothing. ``None`` when the provider says nothing about its keys -- no return
    annotation, a plain ``dict``, a lambda, or a return annotation that does not
    resolve -- so it may fill any name, and a transport requires none of them
    for lacking a default. Held by ``test_a_spec_without_a_provider_fills_nothing``
    and ``test_a_provider_whose_return_says_nothing_is_untyped``.

    **Each annotation costs only what it says.** The return annotation is
    resolved on its own, so a parameter annotation naming a type imported only
    under ``TYPE_CHECKING`` leaves the keys readable
    (``test_a_parameter_type_imported_only_for_type_checking_leaves_the_keys_readable``).
    The keys are read off the ``TypedDict`` without hints, and each value is
    resolved on its own, so one that does not resolve makes only its own key
    declinable: whether the provider may decline it cannot be read, and
    declinable is the reading that neither hides the name from the caller nor
    requires it (``test_a_value_type_that_does_not_resolve_makes_only_its_key_declinable``).
    Python 3.14's lazily evaluated annotations are read the same way, in the
    format that leaves an unresolvable name as a forward reference rather than
    raising (``test_lazily_evaluated_annotations_are_read_one_at_a_time``).

    **A parameterised alias is read through its origin, with its arguments.**
    ``Scope[str | UnsetType]`` does not relay ``__required_keys__``, so the keys
    come off ``Scope``, where a value annotated ``T`` is the bare type variable.
    The alias's arguments are substituted for the origin's parameters wherever
    one appears in a value, a union included
    (``test_a_generic_typed_dicts_arguments_decide_which_keys_may_be_declined``),
    and an alias whose argument does not admit ``UNSET`` still fills the key
    (``test_a_parameterised_typed_dict_is_read_off_its_origin``). A type variable
    no alias substitutes counts as filled, because a ``TypedDict`` subclassing
    ``Scope[str]`` keeps ``tenant: T`` in its own hints while its type is
    ``str`` (``test_a_type_variable_no_alias_substitutes_is_filled``).

    Duck-typed on the keys a ``TypedDict`` class carries rather than
    ``is_typeddict``, because the standard library's answers ``False`` for a
    ``typing_extensions.TypedDict`` on the older Pythons this package supports.
    """
    if provider is None:
        return frozenset(), frozenset()
    try:
        returned = _resolve(
            get_annotations(provider, format=Format.FORWARDREF).get("return"),
            getattr(inspect.unwrap(provider), "__globals__", {}),
        )
        declared: Any = get_origin(returned) or returned
        if getattr(declared, "__required_keys__", None) is None:
            return None
        values = get_annotations(declared, format=Format.FORWARDREF)
    except Exception:
        # A return annotation that does not resolve, or a callable no
        # annotations can be read off: either way nothing usable was declared.
        return None
    names = frozenset(declared.__required_keys__) | frozenset(declared.__optional_keys__)
    # Not ``strict``: a bare ``-> Scope`` gives no arguments, and its parameters stay free.
    arguments = dict(zip(getattr(declared, "__parameters__", ()), get_args(returned), strict=False))
    namespace = getattr(sys.modules.get(declared.__module__), "__dict__", {})
    declinable = frozenset(
        name for name in names if _may_decline(values.get(name), namespace, arguments)
    )
    return names - declinable, declinable


def _may_decline(annotation: Any, namespace: dict[str, Any], arguments: Mapping[Any, Any]) -> bool:
    """Whether a key annotated ``annotation`` may come back ``UNSET``; ``True`` if unreadable."""
    try:
        value = _resolve(annotation, namespace)
    except Exception:
        # Most often a name imported only under ``TYPE_CHECKING``. Which way the
        # key reads cannot be known, so it reads the way that asks least.
        return True
    return _admits_unset(value, arguments)


def _resolve(annotation: Any, namespace: dict[str, Any]) -> Any:
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
    """
    holder = SimpleNamespace(__annotations__={"value": annotation})
    return get_type_hints(holder, globalns=namespace)["value"]


def _admits_unset(annotation: Any, arguments: Mapping[Any, Any]) -> bool:
    """Whether a value annotated ``annotation`` may be drf-services' ``UNSET``.

    ``UnsetType`` itself, or a union with it among its alternatives, at any
    depth: ``str | UnsetType``, ``Union[str, UnsetType]``,
    ``Optional[Union[str, UnsetType]]``, and any of those inside a wrapper,
    which ``_resolve`` has already stripped. Any other generic stops the walk,
    because its arguments are not the value: ``list[str | UnsetType]`` is a key
    always filled with a list, and dispatch drops a key only when its value *is*
    ``UNSET``. A type variable is first replaced by the argument its alias gave
    it, at every level.

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
        annotation = arguments.get(annotation, annotation)
    return annotation is UnsetType or (
        get_origin(annotation) in _UNIONS
        and any(_admits_unset(argument, arguments) for argument in get_args(annotation))
    )


__all__ = ["provider_keys"]
