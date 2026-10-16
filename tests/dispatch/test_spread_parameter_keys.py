"""``declared_input_keys`` for a spread service with no serializer.

Dispatch itself is the kernel's now; these pin the set, which a transport reads
without dispatching (drf-mcp asks ``declared_input_keys`` whether a spec's set is
enumerable, without a binding).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import django_filters
import pytest
from rest_framework import serializers
from typing_extensions import TypedDict, Unpack

from rest_framework_services.dispatch.utils import (
    _UnresolvedExtras,
    declared_input_keys,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

_SPREADS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)


def _by_pk(*, pk: int) -> Any:
    return pk


_LOOKUP = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk)


class _Flags(TypedDict, total=False):
    urgent: bool


def _close(
    position: int = 0,
    /,
    *,
    instance: Any,
    user: Any,
    reason: str,
    note: str = "none",
    team: Annotated[str, NotClientInput] = "own-team",
    **flags: Unpack[_Flags],
) -> None: ...


def _spec(service: Any, **kwargs: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=service, instance_selector_spec=_LOOKUP, atomic=False, **kwargs)


@_SPREADS
def test_the_set_is_the_lookup_plus_the_callers_parameters(binding: ArgumentBinding) -> None:
    """Keyword parameters and ``Unpack`` keys join; the positional-only
    ``position``, the reserved ``instance`` / ``user`` and the hidden ``team`` do
    not."""
    keys = declared_input_keys(_spec(_close), serializer=None, argument_binding=binding)

    assert keys == {"pk", "reason", "note", "urgent"}


def test_a_registered_seed_leaves_the_set_too() -> None:
    keys = declared_input_keys(
        _spec(_close),
        serializer=None,
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        reserved=RESERVED_POOL_SEEDS | {"note"},
    )

    assert keys == {"pk", "reason", "urgent"}


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"argument_binding": ArgumentBinding.AUTO}, {"argument_binding": ArgumentBinding.BUNDLE}],
)
def test_without_a_spreading_binding_the_set_is_the_lookup(kwargs: dict[str, Any]) -> None:
    """No keyword is the call a transport makes without a binding, and keeps the
    set it always had."""
    assert declared_input_keys(_spec(_close), serializer=None, **kwargs) == {"pk"}


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def test_a_serializer_keeps_its_set_under_a_spreading_binding() -> None:
    spec = _spec(_close, input_serializer=_TitleSerializer)
    keys = declared_input_keys(
        spec,
        serializer=_TitleSerializer(),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert keys == {"title", "pk"}


def test_a_many_spec_keeps_its_set_under_a_spreading_binding() -> None:
    """Dispatch refuses a spreading binding on ``many=True``; the set is read before
    that refusal by a transport, and still names only the item's fields."""
    spec = ServiceSpec(service=_close, input_serializer=_TitleSerializer, many=True, atomic=False)
    keys = declared_input_keys(
        spec,
        serializer=_TitleSerializer(),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert keys == {"title"}


def test_a_selector_ignores_the_binding() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk)
    keys = declared_input_keys(spec, serializer=None, argument_binding=ArgumentBinding.BUNDLE)

    assert keys == {"pk"}


def _close_open(*, instance: Any, **changes: Any) -> None: ...


@_SPREADS
def test_a_bare_var_keyword_opens_the_set(binding: ArgumentBinding) -> None:
    assert (
        declared_input_keys(_spec(_close_open), serializer=None, argument_binding=binding) is None
    )


if TYPE_CHECKING:

    class _Hidden(TypedDict, total=False):
        tenant: str


def _close_for_tenant(*, instance: Any, **extras: Unpack[_Hidden]) -> None: ...


def test_an_unresolvable_var_keyword_raises() -> None:
    with pytest.raises(_UnresolvedExtras):
        declared_input_keys(
            _spec(_close_for_tenant),
            serializer=None,
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )


def test_an_unresolvable_var_keyword_is_read_only_under_a_spreading_binding() -> None:
    assert declared_input_keys(_spec(_close_for_tenant), serializer=None) == {"pk"}


class _TitleFilter(django_filters.FilterSet):
    title = django_filters.CharFilter()


def test_an_open_lookup_leaves_the_set_open_before_the_service_is_read() -> None:
    """The lookup is read first, so an unresolvable service surface beside an
    open lookup raises nothing here: the set is open whatever the service declares.

    This is ``declared_input_keys`` alone. Dispatching the same spec under
    ``REJECT`` and ``SPREAD_AUTHOR_WINS`` raises ``ImproperlyConfigured``
    (test_reject_beside_a_filtered_lookup_cannot_be_held_to_unresolvable_extras).
    """
    open_lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk, filter_set=_TitleFilter)
    spec = ServiceSpec(service=_close_for_tenant, instance_selector_spec=open_lookup, atomic=False)

    assert (
        declared_input_keys(
            spec, serializer=None, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS
        )
        is None
    )
