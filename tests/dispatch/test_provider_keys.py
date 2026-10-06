"""Tests for ``provider_keys``: what a ``kwargs=`` provider says it fills, before it runs.

Both spec transports carried a copy of this reader, and these are their tests,
moved here from the schema each built: the MCP server's selector-tool
requiredness tests and the Pydantic-AI ``SpecToolset``'s
``test_a_providers_return_annotation_decides_what_the_model_is_asked_for``. Each
asserts on the reader's answer, which is what both schemas were built from.

Three readings both copies got wrong have tests of their own below the moved
ones, each in its own section: a container holding ``UnsetType``, an annotation
that does not resolve, and a generic ``TypedDict``'s arguments.
"""

from __future__ import annotations

import sys
import types
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ForwardRef,
    Generic,
    Optional,
    TypeVar,
    Union,
    get_type_hints,
)

import pytest
from typing_extensions import NotRequired, ReadOnly, Required, TypedDict

from rest_framework_services.dispatch.provider_keys import provider_keys
from rest_framework_services.types.provider_keys import ProviderKeys
from rest_framework_services.types.unset import UNSET, UnsetType
from tests.dispatch.provider_keys_fixtures import passthrough

if TYPE_CHECKING:
    # Annotation-only imports, where a linter's type-checking rules move them.
    # Neither name exists when the tests run, so an annotation naming one does
    # not resolve.
    from django.contrib.auth.models import Group
    from django.http import HttpRequest

_T = TypeVar("_T")

_NOTHING: frozenset[str] = frozenset()


class _ScopeBase(TypedDict):
    tenant: str


class _Scope(_ScopeBase, total=False):
    # ``total=False`` rather than ``NotRequired[...]``: under this module's
    # postponed annotations the qualifier is a string ``TypedDict`` does not
    # read, and ``region`` would land in ``__required_keys__``.
    region: str


class _GenericScope(TypedDict, Generic[_T]):
    tenant: _T


def _typed_provider() -> _Scope:
    return {"tenant": "acme"}


def _generic_provider() -> _GenericScope[str]:
    return {"tenant": "acme"}


def _plain_dict_provider() -> dict[str, Any]:
    return {"tenant": "acme"}


def _unresolvable_provider() -> Any:
    return {"tenant": "acme"}


# A return annotation naming a type that does not exist. Assigned rather than
# written in the signature, where a linter would refuse the undefined name.
_unresolvable_provider.__annotations__ = {"return": "NoSuchScope"}


def test_a_spec_without_a_provider_fills_nothing() -> None:
    # Nothing, which is not the "cannot tell" of an untyped provider: a
    # transport requires every parameter without a default beside this one,
    # and none beside that one.
    assert provider_keys(None) == (_NOTHING, _NOTHING)


def test_every_key_of_a_typed_dict_is_filled_optional_ones_included() -> None:
    # ``region`` is optional in the ``TypedDict``, but the provider owns it, so
    # it is filled as much as ``tenant`` is.
    assert provider_keys(_typed_provider) == (frozenset({"tenant", "region"}), _NOTHING)


def test_a_parameterised_typed_dict_is_read_off_its_origin() -> None:
    # The alias does not relay ``__required_keys__``; its origin does.
    assert provider_keys(_generic_provider) == (frozenset({"tenant"}), _NOTHING)


@pytest.mark.parametrize(
    "provider",
    [_plain_dict_provider, lambda view: {"tenant": "acme"}, _unresolvable_provider],
    ids=["plain-dict", "unannotated", "unresolvable-return"],
)
def test_a_provider_whose_return_says_nothing_is_untyped(provider: Any) -> None:
    # ``None``: it may fill any name, so a transport requires none of them.
    assert provider_keys(provider) is None


# ``UnsetType`` in a value: drf-services lets a provider decline a key with
# ``UNSET``, which ``resolve_provider`` removes from the pool, so a declined key
# does not satisfy the parameter and is returned apart from the filled ones.
class _MaybeScope(TypedDict):
    tenant: str | UnsetType
    region: str


class _MaybeUnionScope(TypedDict):
    tenant: Union[str, UnsetType]  # noqa: UP007 -- the spelling under test
    region: str


class _MaybeOptionalUnionScope(TypedDict):
    tenant: Optional[Union[str, UnsetType]]  # noqa: UP007, UP045 -- the spelling under test
    region: str


# Each wrapper around the union. ``typing_extensions``' hints strip all four on
# every supported Python, which the standard library's did not do for
# ``NotRequired`` on 3.10, so each spelling holds that the stripping happens.
class _MaybeNotRequiredScope(TypedDict):
    tenant: NotRequired[str | UnsetType]
    region: str


class _MaybeRequiredScope(TypedDict, total=False):
    tenant: Required[str | UnsetType]
    region: Required[str]


class _MaybeReadOnlyScope(TypedDict):
    tenant: ReadOnly[str | UnsetType]
    region: str


class _MaybeAnnotatedScope(TypedDict):
    tenant: Annotated[str | UnsetType, "the caller's when the provider declines"]
    region: str


# The same wrapper through the functional syntax, which the postponed
# annotations above do not hide from the ``TypedDict`` machinery.
_FunctionalMaybeScope = TypedDict(  # noqa: UP013
    "_FunctionalMaybeScope",
    {"tenant": NotRequired[str | UnsetType], "region": str},
)


def _maybe_provider() -> _MaybeScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_union_provider() -> _MaybeUnionScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_optional_union_provider() -> _MaybeOptionalUnionScope:
    return {"tenant": None, "region": "eu"}


def _maybe_not_required_provider() -> _MaybeNotRequiredScope:
    return {"region": "eu"}


def _maybe_required_provider() -> _MaybeRequiredScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_read_only_provider() -> _MaybeReadOnlyScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_annotated_provider() -> _MaybeAnnotatedScope:
    return {"tenant": UNSET, "region": "eu"}


def _functional_maybe_provider() -> _FunctionalMaybeScope:
    return {"region": "eu"}


@pytest.mark.parametrize(
    "provider",
    [
        _maybe_provider,
        _maybe_union_provider,
        _maybe_optional_union_provider,
        _maybe_not_required_provider,
        _functional_maybe_provider,
        _maybe_required_provider,
        _maybe_read_only_provider,
        _maybe_annotated_provider,
    ],
    ids=[
        "pep-604-union",
        "typing-union",
        "optional-union",
        "not-required",
        "functional-not-required",
        "required",
        "read-only",
        "annotated",
    ],
)
def test_a_key_whose_value_admits_unset_may_be_declined(provider: Any) -> None:
    assert provider_keys(provider) == (frozenset({"region"}), frozenset({"tenant"}))


class _NoneableScope(TypedDict):
    tenant: str | None
    region: str


def _noneable_provider() -> _NoneableScope:
    return {"tenant": None, "region": "eu"}


@pytest.mark.parametrize(
    ("provider", "filled", "declinable"),
    [(None, _NOTHING, _NOTHING), (_maybe_provider, frozenset({"region"}), frozenset({"tenant"}))],
    ids=["no-provider", "typed-provider"],
)
def test_the_answer_names_its_two_sets_and_still_unpacks(
    provider: Any, filled: frozenset[str], declinable: frozenset[str]
) -> None:
    # The Pydantic-AI toolset reads the two sets by name and the MCP server
    # unpacks them, so one answer serves both call sites. One case per return
    # statement, so neither can go back to a bare tuple.
    keys = provider_keys(provider)

    assert isinstance(keys, ProviderKeys)
    assert (keys.filled, keys.declinable) == (filled, declinable)
    unpacked_filled, unpacked_declinable = keys
    assert (unpacked_filled, unpacked_declinable) == (filled, declinable)


def test_a_union_that_does_not_admit_unset_is_still_filled() -> None:
    # Having arguments is not enough: ``str | None`` is a union, and ``None`` is
    # a value the provider fills the key with, not a refusal to.
    assert provider_keys(_noneable_provider) == (frozenset({"tenant", "region"}), _NOTHING)


# ---------- A key holding ``UnsetType`` inside a container is filled ----------


class _ContainerScope(TypedDict):
    # Each of the first four values holds ``UnsetType``, but as an item of a
    # list, a dict or a tuple. The key is always filled with the container, and
    # ``resolve_provider`` drops a key only when its value *is* ``UNSET``.
    regions: list[str | UnsetType]
    labels: dict[str, str | UnsetType]
    codes: list[UnsetType]
    pair: tuple[int, UnsetType]
    tenant: str | UnsetType


def _container_provider() -> _ContainerScope:
    return {"regions": ["eu"], "labels": {}, "codes": [], "pair": (1, UNSET), "tenant": UNSET}


def test_a_container_of_unset_is_not_a_key_the_provider_may_decline() -> None:
    assert provider_keys(_container_provider) == (
        frozenset({"regions", "labels", "codes", "pair"}),
        frozenset({"tenant"}),
    )


# ---------- One annotation that does not resolve costs only what it says ----------


def _hidden_parameter_provider(*, request: HttpRequest) -> _Scope:
    return {"tenant": "acme"}


def test_a_parameter_type_imported_only_for_type_checking_leaves_the_keys_readable() -> None:
    # A parameter's annotation says nothing about the keys. Beside a selector
    # ``(*, pk, tenant, region)`` this provider still fills ``tenant`` and
    # ``region``, so ``pk`` is the one name left for the caller, and required.
    assert provider_keys(_hidden_parameter_provider) == (frozenset({"tenant", "region"}), _NOTHING)


class _HiddenValueScope(TypedDict):
    tenant: Group
    region: str


def _hidden_value_provider() -> _HiddenValueScope:
    return {"region": "eu"}


def test_a_value_type_that_does_not_resolve_makes_only_its_key_declinable() -> None:
    # The keys need no hints, so they are still read. Whether ``tenant`` may be
    # declined cannot be, so it is counted as a key the provider may decline:
    # left for the caller to send, never required. ``region`` stays filled.
    assert provider_keys(_hidden_value_provider) == (frozenset({"region"}), frozenset({"tenant"}))


# A forward reference built by hand carries no module, where every one the
# class and functional syntaxes build does. It resolves in the module that
# defined the ``TypedDict``, which is the only reading that needs that module.
_HandBuiltScope = TypedDict(  # noqa: UP013
    "_HandBuiltScope",
    {"regions": ForwardRef("list[UnsetType]")},
)


def _hand_built_provider() -> _HandBuiltScope:
    return {"regions": []}


def test_a_forward_reference_without_a_module_resolves_where_the_typed_dict_was_defined() -> None:
    # Resolved, ``regions`` is a list and filled; unresolved, it would read as
    # one the provider may decline.
    assert provider_keys(_hand_built_provider) == (frozenset({"regions"}), _NOTHING)


# ---------- A generic ``TypedDict`` is read with its alias's arguments ----------


def _declining_generic_provider() -> _GenericScope[str | UnsetType]:
    return {"tenant": UNSET}


class _NestedGenericScope(TypedDict, Generic[_T]):
    tenant: _T | None


def _nested_declining_generic_provider() -> _NestedGenericScope[UnsetType]:
    return {"tenant": UNSET}


def _nested_union_generic_provider() -> _NestedGenericScope[str | UnsetType]:
    return {"tenant": UNSET}


@pytest.mark.parametrize(
    "provider",
    [
        _declining_generic_provider,
        _nested_declining_generic_provider,
        _nested_union_generic_provider,
        passthrough(_declining_generic_provider),
    ],
    ids=[
        "argument-is-the-value",
        "argument-inside-a-union",
        "union-argument-inside-a-union",
        "decorated-elsewhere",
    ],
)
def test_a_generic_typed_dicts_arguments_decide_which_keys_may_be_declined(provider: Any) -> None:
    # ``tenant: _T`` read off the origin is the bare type variable. The alias
    # says what it stands for, at whatever depth it appears, and what it stands
    # for is walked in turn: in ``union-argument-inside-a-union`` the argument
    # is itself a union, met inside ``_T | None``, so it is not enough to ask
    # whether a substituted argument *is* ``UnsetType``. ``decorated-elsewhere``
    # is the first case behind a wrapper from another module, whose globals
    # know no ``_GenericScope``: the annotation resolves in the decorated
    # function's.
    assert provider_keys(provider) == (_NOTHING, frozenset({"tenant"}))


# ---------- A subclass binds a generic ``TypedDict``'s parameters ----------

_U = TypeVar("_U")


class _ConcreteScope(_GenericScope[str]):
    region: str


class _DecliningScope(_GenericScope[str | UnsetType]):
    # No alias says what ``tenant: _T`` stands for; the class statement does,
    # and only ``__orig_bases__`` remembers it. Its own hints keep ``_T``.
    region: str


class _DecliningSubScope(_DecliningScope):
    # One level further, through a base that is a class rather than an alias.
    note: str


class _RelayScope(_GenericScope[_U], Generic[_U]):
    # Hands its own parameter on, so its alias decides what ``_T`` is.
    region: str


class _OptionalRelayScope(_GenericScope[_U | None], Generic[_U]):
    # Hands its parameter on inside a union, which is substituted there.
    region: str


class _RecurringScope(_GenericScope[_T | UnsetType], Generic[_T]):
    # The same type variable at both levels, its base's bound in terms of its
    # own. ``tenant`` is declared on the base, so it reads the base's binding.
    region: str


def _concrete_provider() -> _ConcreteScope:
    return {"tenant": "acme", "region": "eu"}


def _declining_subclass_provider() -> _DecliningScope:
    return {"tenant": UNSET, "region": "eu"}


def _declining_sub_subclass_provider() -> _DecliningSubScope:
    return {"tenant": UNSET, "region": "eu", "note": ""}


def _relaying_provider() -> _RelayScope[str | UnsetType]:
    return {"tenant": UNSET, "region": "eu"}


def _filling_relaying_provider() -> _RelayScope[str]:
    return {"tenant": "acme", "region": "eu"}


def _optional_relaying_provider() -> _OptionalRelayScope[str | UnsetType]:
    return {"tenant": UNSET, "region": "eu"}


def _recurring_provider() -> _RecurringScope[str]:
    return {"tenant": UNSET, "region": "eu"}


def _bare_recurring_provider() -> _RecurringScope:  # the bare alias, under test
    return {"tenant": UNSET, "region": "eu"}


_FILLED_TENANT = (frozenset({"tenant", "region"}), _NOTHING)
_DECLINABLE_TENANT = (frozenset({"region"}), frozenset({"tenant"}))


@pytest.mark.parametrize(
    ("provider", "keys"),
    [
        (_concrete_provider, _FILLED_TENANT),
        (_declining_subclass_provider, _DECLINABLE_TENANT),
        (_declining_sub_subclass_provider, (frozenset({"region", "note"}), frozenset({"tenant"}))),
        (_relaying_provider, _DECLINABLE_TENANT),
        (_filling_relaying_provider, _FILLED_TENANT),
        (_optional_relaying_provider, _DECLINABLE_TENANT),
        (_recurring_provider, _DECLINABLE_TENANT),
        (_bare_recurring_provider, _DECLINABLE_TENANT),
    ],
    ids=[
        "binds-a-filled-type",
        "binds-a-declinable-type",
        "through-a-class-base",
        "relays-a-declinable-type",
        "relays-a-filled-type",
        "relays-inside-a-union",
        "rebinds-its-own-type-variable",
        "bare-rebinding",
    ],
)
def test_a_subclass_binding_decides_which_keys_may_be_declined(
    provider: Any, keys: tuple[frozenset[str], frozenset[str]]
) -> None:
    # Reading ``tenant: _T`` as free in any of these would count it filled, and
    # a provider returning ``UNSET`` for it would then leave a parameter nobody
    # was asked for: the call fails with a ``TypeError``.
    assert provider_keys(provider) == keys


def _free_generic_provider() -> _GenericScope:  # the bare alias, under test
    return {"tenant": "acme"}


def _free_relaying_provider() -> _RelayScope:  # the bare alias, under test
    return {"tenant": "acme", "region": "eu"}


@pytest.mark.parametrize(
    "provider", [_free_generic_provider, _free_relaying_provider], ids=["bare-alias", "bare-relay"]
)
def test_a_type_variable_nothing_binds_is_filled(provider: Any) -> None:
    # Whether a free ``_T`` may be ``UNSET`` is not written anywhere. Filled
    # hides the key from the caller, and the provider decides it. Declinable
    # would let a transport whose caller's value wins put that value where the
    # provider was meant to decide, such as a tenant.
    filled, declinable = provider_keys(provider) or (_NOTHING, _NOTHING)

    assert "tenant" in filled
    assert declinable == _NOTHING


# ---------- Python 3.14 evaluates annotations lazily ----------

_LAZY_SOURCE = """
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from rest_framework_services.types.unset import UnsetType

if TYPE_CHECKING:
    from django.http import HttpRequest


class Scope(TypedDict):
    tenant: HttpRequest
    note: str | UnsetType
    region: str


def provider(*, request: HttpRequest) -> Scope:
    return {"region": "eu"}
"""


@pytest.mark.skipif(sys.version_info < (3, 14), reason="eager annotations raise at definition")
def test_lazily_evaluated_annotations_are_read_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Compiled without this module's postponed annotations, so the provider and
    # its ``TypedDict`` keep 3.14's lazily evaluated ones, where reading them all
    # at once raises for the one ``HttpRequest`` names.
    module = types.ModuleType("lazy_providers")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(_LAZY_SOURCE, module.__name__, "exec", dont_inherit=True), module.__dict__)

    assert provider_keys(module.provider) == (
        frozenset({"region"}),
        frozenset({"tenant", "note"}),
    )


# ---------- A PEP 695 generic ``TypedDict`` under postponed annotations ----------

_PEP_695_SOURCE = """
from __future__ import annotations

from typing_extensions import TypedDict

from rest_framework_services.types.unset import UnsetType


class Scope[T](TypedDict):
    tenant: T
    tags: list[T]


class Declining(Scope[str | UnsetType]):
    ...


class Recurring[T](Scope[T | UnsetType]):
    ...


def provider() -> {annotation}:
    return {{}}
"""


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax is new in 3.12")
@pytest.mark.parametrize(
    ("annotation", "keys"),
    [
        ("Scope[str]", (frozenset({"tenant", "tags"}), _NOTHING)),
        ("Scope[str | UnsetType]", (frozenset({"tags"}), frozenset({"tenant"}))),
        ("Scope", (frozenset({"tenant", "tags"}), _NOTHING)),
        ("Declining", (frozenset({"tags"}), frozenset({"tenant"}))),
        ("Recurring[str]", (frozenset({"tags"}), frozenset({"tenant"}))),
    ],
    ids=["filled", "declinable", "bare-alias", "subclass", "same-name-rebound"],
)
def test_a_pep_695_type_parameter_is_read_as_the_class_declares_it(
    monkeypatch: pytest.MonkeyPatch,
    annotation: str,
    keys: tuple[frozenset[str], frozenset[str]],
) -> None:
    # ``T`` lives in ``Scope.__type_params__``, never in the module, so the
    # string ``"T"`` a postponed annotation leaves resolves only beside them.
    # ``tags: list[T]`` is always filled with a list once ``T`` resolves, and a
    # value that does not resolve reads as declinable, so ``tags`` filled is
    # what shows the keys were read by resolution rather than by failing to.
    # Compiled afresh for each case: up to 3.13 a forward reference can keep
    # the value it last resolved to, and one case must not answer for the next.
    # ``same-name-rebound`` has two parameters named ``T``; the one that
    # declares ``tenant`` is ``Scope``'s, bound to ``str | UnsetType``.
    module = types.ModuleType("pep_695_providers")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = _PEP_695_SOURCE.format(annotation=annotation)
    exec(compile(source, module.__name__, "exec", dont_inherit=True), module.__dict__)

    assert provider_keys(module.provider) == keys


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax is new in 3.12")
def test_a_type_parameter_resolved_elsewhere_first_does_not_decide_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``Scope`` and ``Recurring`` share one forward reference ``"T"``, and up
    # to 3.13 it answers with the value it last resolved to whenever it is
    # resolved again in one namespace. Resolving ``Recurring``'s hints first leaves
    # ``Recurring``'s ``T`` there, which nothing binds under ``Scope[...]``, so
    # a reader that let it answer would count ``tenant`` filled.
    module = types.ModuleType("pep_695_primed_providers")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = _PEP_695_SOURCE.format(annotation="Scope[str | UnsetType]")
    exec(compile(source, module.__name__, "exec", dont_inherit=True), module.__dict__)
    get_type_hints(module.Recurring)

    assert provider_keys(module.provider) == (frozenset({"tags"}), frozenset({"tenant"}))
