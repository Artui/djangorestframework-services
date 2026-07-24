"""Unit tests for the dispatch input-policy helpers (no DB)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from rest_framework.exceptions import ValidationError
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services.dispatch.utils import (
    RESERVED_POOL_SEEDS,
    call_target_guard,
    declared_input_keys,
    merge_arguments,
    resolve_argument_binding,
    resolve_provider,
    resolve_unknown_arguments,
    service_input,
    view_url_kwargs,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET


@dataclass
class _PostIn:
    title: str


class _ChildExtras(TypedDict, total=False):
    parent_pk: int
    label: NotRequired[str]


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

    def test_url_kwargs_author_wins_beats_spread_below_provider(self) -> None:
        # AUTHOR_WINS (the selector default): url_kwargs out-rank the client
        # spread (route scope is authoritative) but the provider still wins.
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            spread_source={"scope": "client", "only_client": 1},
            provider_kwargs={"owned": "author"},
            url_kwargs={"scope": "route", "from_url": 2},
        )
        assert pool == {"scope": "route", "only_client": 1, "owned": "author", "from_url": 2}

    def test_url_kwargs_provider_still_wins_on_conflict(self) -> None:
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            spread_source={},
            provider_kwargs={"scope": "author"},
            url_kwargs={"scope": "route"},
        )
        assert pool == {"scope": "author"}

    def test_url_kwargs_caller_wins_spread_out_ranks_url(self) -> None:
        # CALLER_WINS: the caller opts to override author-supplied context,
        # including the route scope.
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.SPREAD_CALLER_WINS,
            spread_source={"scope": "client"},
            provider_kwargs={"scope": "author"},
            url_kwargs={"scope": "route"},
        )
        assert pool == {"scope": "client"}

    def test_url_kwargs_bundle_adds_url_below_provider(self) -> None:
        pool: dict[str, Any] = {}
        merge_arguments(
            pool,
            binding=ArgumentBinding.BUNDLE,
            spread_source={"ignored": 1},
            provider_kwargs={"k": "author"},
            url_kwargs={"k": "route", "u": 3},
        )
        assert pool == {"k": "author", "u": 3}


class TestViewUrlKwargs:
    def test_returns_kwargs_stripping_reserved_seeds(self) -> None:
        view = SimpleNamespace(kwargs={"parent_pk": 5, "user": "spoof", "ok": 1})
        assert view_url_kwargs(view) == {"parent_pk": 5, "ok": 1}

    def test_empty_when_no_kwargs(self) -> None:
        assert view_url_kwargs(SimpleNamespace(kwargs={})) == {}
        assert view_url_kwargs(SimpleNamespace(kwargs=None)) == {}

    def test_empty_when_view_has_no_kwargs_attr(self) -> None:
        assert view_url_kwargs(None) == {}


class TestResolveProviderUnset:
    def test_none_provider_is_empty(self) -> None:
        assert resolve_provider(None, {}) == {}

    def test_drops_unset_keys_keeps_the_rest(self) -> None:
        def provider(**_: Any) -> dict[str, Any]:
            return {"role": UNSET, "kept": "value", "none_is_kept": None}

        # UNSET means "declined" and is dropped; a real ``None`` is preserved.
        assert resolve_provider(provider, {}) == {"kept": "value", "none_is_kept": None}


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

    def test_selector_unresolvable_unpack_hints_stay_open(self) -> None:
        def selector(**extras: Unpack[_Ghost]) -> Any: ...  # noqa: F821 — unresolvable

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
        assert declared_input_keys(spec, serializer=None) is None

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
