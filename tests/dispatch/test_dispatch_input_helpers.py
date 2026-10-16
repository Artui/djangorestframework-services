"""Unit tests for the dispatch input-policy helpers (no DB)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import ValidationError
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services.dispatch.utils import (
    _UnresolvedExtras,
    declared_input_keys,
    resolve_argument_binding,
    resolve_unknown_arguments,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unknown_arguments import UnknownArguments


@dataclass
class _PostIn:
    title: str


class _ChildExtras(TypedDict, total=False):
    parent_pk: int
    label: NotRequired[str]


def _service(**_kwargs: object) -> None: ...


def _by_pk(*, pk: int) -> Any:
    return pk


def _by_parent(*, parent_pk: int) -> Any:
    return parent_pk


def _open_selector(**_kwargs: Any) -> Any: ...


if TYPE_CHECKING:
    # The routine idiom this module's ``from __future__ import annotations`` makes
    # possible: a TypedDict that only exists for the type checker, so the runtime
    # cannot resolve the annotation that names it.
    class _TenantExtras(TypedDict, total=False):
        tenant: str


def _find_orders(*, user: Any, **extras: Unpack[_TenantExtras]) -> Any: ...


class _FilterSet:
    def __init__(self, *, data: Any, queryset: Any) -> None: ...


class TestResolveArgumentBinding:
    def test_auto_service_is_bundle(self) -> None:
        spec = ServiceSpec(service=_service)
        assert resolve_argument_binding(spec, ArgumentBinding.AUTO) is ArgumentBinding.BUNDLE

    def test_auto_selector_is_spread_author_wins(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_open_selector)
        assert (
            resolve_argument_binding(spec, ArgumentBinding.AUTO)
            is ArgumentBinding.SPREAD_AUTHOR_WINS
        )

    def test_explicit_mode_passes_through(self) -> None:
        spec = ServiceSpec(service=_service)
        assert (
            resolve_argument_binding(spec, ArgumentBinding.SPREAD_CALLER_WINS)
            is ArgumentBinding.SPREAD_CALLER_WINS
        )


class TestDeclaredInputKeys:
    def test_selector_filter_set_is_open(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_by_pk, filter_set=_FilterSet)
        assert declared_input_keys(spec, serializer=None) is None

    def test_selector_var_keyword_is_open(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_open_selector)
        assert declared_input_keys(spec, serializer=None) is None

    def test_selector_enumerates_params(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_by_pk)
        assert declared_input_keys(spec, serializer=None) == {"pk"}

    def test_selector_unpack_var_keyword_enumerates_typed_dict_keys(self) -> None:
        # A ``**extras: Unpack[TypedDict]`` selector is *closed* — its keys are
        # enumerable, so ``REJECT`` can accept them and reject strangers.
        # ``*args`` is skipped, ``pk`` is enumerated, extras are expanded.
        def selector(pk: int, *args: Any, **extras: Unpack[_ChildExtras]) -> Any: ...

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
        assert declared_input_keys(spec, serializer=None) == {"pk", "parent_pk", "label"}

    def test_selector_unresolvable_unpack_hints_raise_instead_of_reading_as_open(
        self,
    ) -> None:
        # "We cannot tell what this accepts" must not collapse into "it accepts
        # anything" — that is what let a strict policy pass everything.
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_find_orders)
        with pytest.raises(_UnresolvedExtras) as exc:
            declared_input_keys(spec, serializer=None)
        assert "_find_orders(**extras)" in str(exc.value)

    def test_selector_unannotated_var_keyword_stays_open(self) -> None:
        # A bare ``**kwargs`` has no annotation to resolve and really does accept
        # anything, so it stays open and never reaches hint resolution.
        def selector(**kwargs):  # the missing annotation is the point of the test
            ...

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
        assert declared_input_keys(spec, serializer=None) is None

    def test_service_nested_selector_with_unresolvable_extras_raises(self) -> None:
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_find_orders),
        )
        with pytest.raises(_UnresolvedExtras):
            declared_input_keys(spec, serializer=None)

    def test_selector_loses_a_key_its_precondition_hides(self) -> None:
        """A key one callable in the call hides is the server's for all of them, so
        a selector naming it plainly does not declare it."""

        def gate(*, tenant: Annotated[str, NotClientInput] = "own") -> None: ...

        def rows(*, tenant: str = "own", limit: int = 10) -> Any: ...

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=rows, preconditions=[gate])
        assert declared_input_keys(spec, serializer=None) == {"limit"}

    def test_selector_without_selector_is_empty(self) -> None:
        # Unreachable through dispatch (a None selector raises first), but the
        # guard must still hold for direct callers.
        spec = SelectorSpec(kind=SelectorKind.LIST)
        assert declared_input_keys(spec, serializer=None) == set()

    def test_service_serializer_fields_plus_nested(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
        )
        assert declared_input_keys(spec, serializer=serializer) == {"title", "pk"}

    def test_service_no_serializer_no_nested_is_empty(self) -> None:
        spec = ServiceSpec(service=_service)
        assert declared_input_keys(spec, serializer=None) == set()

    def test_service_nested_selector_without_selector_contributes_nothing(self) -> None:
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE),
        )
        assert declared_input_keys(spec, serializer=None) == set()

    def test_service_nested_filter_set_is_open(self) -> None:
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_by_pk, filter_set=_FilterSet
            ),
        )
        assert declared_input_keys(spec, serializer=None) is None

    def test_service_collection_lookup_alone_declares_its_keys(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        spec = ServiceSpec(
            service=_service,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_by_parent),
        )
        assert declared_input_keys(spec, serializer=serializer) == {"title", "parent_pk"}

    def test_service_many_declares_no_lookup_keys(self) -> None:
        # A ``many=True`` dispatch resolves no target at all, so only the item
        # serializer's fields are declared. Either lookup beside ``many`` is refused
        # when the spec is built (tests/types/test_service_spec.py), so the spec here
        # declares none, and under ``AUTO`` the service's parameters join nothing.
        serializer = SimpleNamespace(fields={"title": object()})
        spec = ServiceSpec(service=_service, many=True)
        assert declared_input_keys(spec, serializer=serializer) == {"title"}


class TestResolveUnknownArguments:
    def _spec(self) -> ServiceSpec[Any, Any, Any]:
        return ServiceSpec(service=_service)

    def test_ignore_is_noop(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        assert (
            resolve_unknown_arguments(
                self._spec(),
                {"bogus": 1},
                unknown_arguments=UnknownArguments.IGNORE,
                serializer=serializer,
            )
            == {}
        )

    def test_open_spec_is_noop(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_open_selector)
        assert (
            resolve_unknown_arguments(
                spec, {"x": 1}, unknown_arguments=UnknownArguments.REJECT, serializer=None
            )
            == {}
        )

    def test_reject_refuses_an_unresolvable_extras_annotation(self) -> None:
        # The whole point of REJECT is refusing an argument it does not recognise.
        # Against a surface it cannot read it used to accept everything in silence.
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_find_orders)
        with pytest.raises(ImproperlyConfigured) as exc:
            resolve_unknown_arguments(
                spec, {"bogus": 1}, unknown_arguments=UnknownArguments.REJECT, serializer=None
            )
        message = str(exc.value)
        assert "REJECT cannot be enforced" in message
        assert "_find_orders(**extras)" in message
        assert "TYPE_CHECKING" in message
        # The message offers only what works: ``PASSTHROUGH`` is retired and raises
        # ``ValueError``, so naming it as a way out sends the reader to a second error.
        assert "PASSTHROUGH" not in message
        assert message.endswith("or dispatch with UnknownArguments.IGNORE.")

    def test_reject_refuses_even_when_no_argument_looks_unknown(self) -> None:
        # The policy is unenforceable regardless of the arguments in hand, so it
        # fails on the first dispatch rather than only on a bogus one.
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_find_orders)
        with pytest.raises(ImproperlyConfigured):
            resolve_unknown_arguments(
                spec, {}, unknown_arguments=UnknownArguments.REJECT, serializer=None
            )

    def test_passthrough_treats_an_unresolvable_extras_annotation_as_open(self) -> None:
        # A permissive policy loses nothing by treating the surface as open, which
        # is exactly what it does with a bare ``**kwargs``.
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_find_orders)
        assert (
            resolve_unknown_arguments(
                spec,
                {"bogus": 1},
                unknown_arguments=UnknownArguments.PASSTHROUGH,
                serializer=None,
            )
            == {}
        )

    def test_ignore_never_inspects_the_declared_surface(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_find_orders)
        assert (
            resolve_unknown_arguments(
                spec, {"bogus": 1}, unknown_arguments=UnknownArguments.IGNORE, serializer=None
            )
            == {}
        )

    def test_no_unknown_is_noop(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        assert (
            resolve_unknown_arguments(
                self._spec(),
                {"title": "t"},
                unknown_arguments=UnknownArguments.REJECT,
                serializer=serializer,
            )
            == {}
        )

    def test_reject_raises_listing_keys(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        with pytest.raises(ValidationError) as exc:
            resolve_unknown_arguments(
                self._spec(),
                {"title": "t", "bogus": 1, "stray": 2},
                unknown_arguments=UnknownArguments.REJECT,
                serializer=serializer,
            )
        message = str(exc.value)
        assert "bogus" in message and "stray" in message

    def test_passthrough_returns_unknown(self) -> None:
        serializer = SimpleNamespace(fields={"title": object()})
        assert resolve_unknown_arguments(
            self._spec(),
            {"title": "t", "note": "n"},
            unknown_arguments=UnknownArguments.PASSTHROUGH,
            serializer=serializer,
        ) == {"note": "n"}

    def test_reserved_seeds_never_unknown(self) -> None:
        spec = ServiceSpec(service=_service)
        assert (
            resolve_unknown_arguments(
                spec,
                {"data": 1, "instance": 2},
                unknown_arguments=UnknownArguments.REJECT,
                serializer=None,
            )
            == {}
        )
