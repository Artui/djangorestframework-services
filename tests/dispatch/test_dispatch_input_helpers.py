"""Unit tests for the dispatch input-policy helpers (no DB)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from rest_framework.exceptions import ValidationError

from rest_framework_services.dispatch.utils import (
    RESERVED_POOL_SEEDS,
    call_target_guard,
    declared_input_keys,
    merge_arguments,
    resolve_argument_binding,
    resolve_unknown_arguments,
    service_input,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unknown_arguments import UnknownArguments


@dataclass
class _PostIn:
    title: str


def _service(**_kwargs: object) -> None: ...


def _by_pk(*, pk: int) -> Any:
    return pk


def _open_selector(**_kwargs: Any) -> Any: ...


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


class TestMergeArguments:
    def test_bundle_adds_only_provider(self) -> None:
        pool: dict[str, Any] = {"user": 1}
        merge_arguments(
            pool,
            binding=ArgumentBinding.BUNDLE,
            spread_source={"a": "client"},
            provider_kwargs={"p": "author"},
        )
        assert pool == {"user": 1, "p": "author"}

    def test_spread_author_wins(self) -> None:
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            spread_source={"x": "client", "only_client": 1},
            provider_kwargs={"x": "author"},
        )
        assert pool == {"x": "author", "only_client": 1}

    def test_spread_caller_wins(self) -> None:
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_CALLER_WINS,
            spread_source={"x": "client"},
            provider_kwargs={"x": "author", "only_author": 1},
        )
        assert pool == {"x": "client", "only_author": 1}

    def test_reserved_seeds_stripped_from_spread(self) -> None:
        pool: dict[str, Any] = {"user": "real", "request": "real"}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_CALLER_WINS,
            spread_source={"user": "spoof", "data": "spoof", "ok": 1},
            provider_kwargs={},
        )
        assert pool == {"user": "real", "request": "real", "ok": 1}
        assert {"user", "request", "data", "instance", "collection"} <= RESERVED_POOL_SEEDS


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


class TestServiceInput:
    def test_no_serializer_no_extras(self) -> None:
        assert service_input(None, {}) == (None, {})

    def test_no_serializer_with_extras(self) -> None:
        data, spread = service_input(None, {"note": "n"})
        assert data == {"note": "n"}
        assert spread == {"note": "n"}

    def test_dict_validated_no_extras_returns_same_object(self) -> None:
        serializer = SimpleNamespace(validated_data={"title": "t"})
        data, spread = service_input(serializer, {})
        assert data is serializer.validated_data
        assert spread is data

    def test_dict_validated_merges_extras(self) -> None:
        serializer = SimpleNamespace(validated_data={"title": "t"})
        data, spread = service_input(serializer, {"note": "n"})
        assert data == {"title": "t", "note": "n"}
        assert spread == data

    def test_dataclass_validated_keeps_instance_and_spreads_extras(self) -> None:
        validated = _PostIn(title="t")
        serializer = SimpleNamespace(validated_data=validated)
        data, spread = service_input(serializer, {"note": "n"})
        assert data is validated
        assert spread == {"note": "n"}


class TestCallTargetGuard:
    def test_none_is_noop(self) -> None:
        call_target_guard(
            None, ServiceSpec(service=_service), object(), user=1, request=None, view=None
        )

    def test_invokes_guard_with_context_and_target(self) -> None:
        seen: dict[str, Any] = {}
        spec = ServiceSpec(service=_service)
        target = object()

        def guard(s: Any, context: Any, *, instance: Any = None) -> None:
            seen["spec"] = s
            seen["user"] = context.user
            seen["instance"] = instance

        call_target_guard(guard, spec, target, user="bob", request=None, view=None)
        assert seen == {"spec": spec, "user": "bob", "instance": target}
