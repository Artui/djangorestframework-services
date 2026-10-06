"""``declared_input_keys`` and ``service_extras`` for a spread service with no serializer.

The dispatch-level behaviour is in ``test_dispatch_spread_parameters.py``; these pin
the set itself, which a transport may read without dispatching (drf-mcp asks
``declared_input_keys`` whether a spec's set is enumerable, without a binding).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import pytest
from rest_framework import serializers
from typing_extensions import TypedDict, Unpack

from rest_framework_services.dispatch.utils import (
    _UnresolvedExtras,
    declared_input_keys,
    service_extras,
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


# --- service_extras ---------------------------------------------------------------

_SENT = {"pk": 1, "reason": "dup", "user": "spoofed", "team": "other", "colour": "red"}


def test_service_extras_takes_the_declared_parameters_beside_the_passthrough() -> None:
    """The positional-only ``position`` is not taken, though the caller sent it."""
    extras = service_extras(
        _spec(_close),
        {**_SENT, "position": 3},
        {"colour": "red"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        reserved=RESERVED_POOL_SEEDS,
    )

    assert extras == {"reason": "dup", "colour": "red"}


@pytest.mark.parametrize("service", [_close_open, _close_for_tenant], ids=["bare", "unresolvable"])
def test_service_extras_takes_every_key_but_the_lookups_from_an_open_service(
    service: Any,
) -> None:
    """Every key but the reserved ``user`` and the lookup's ``pk``, which reaches a
    service only by name. ``team`` is still here: the dispatch site strips hidden
    keys from the result."""
    extras = service_extras(
        _spec(service),
        _SENT,
        {},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        reserved=RESERVED_POOL_SEEDS,
    )

    assert extras == {"reason": "dup", "team": "other", "colour": "red"}


def _all_posts() -> Any: ...


class _ByPk:
    """A duck-typed ``filter_set``: the core cannot enumerate what it reads."""


def test_service_extras_withholds_only_what_the_lookup_names() -> None:
    """A key the lookup reads only through its ``filter_set`` is not one it names,
    so an open service still takes it."""
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_all_posts, filter_set=_ByPk)
    extras = service_extras(
        ServiceSpec(service=_close_open, instance_selector_spec=lookup, atomic=False),
        _SENT,
        {},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        reserved=RESERVED_POOL_SEEDS,
    )

    assert extras == {"pk": 1, "reason": "dup", "team": "other", "colour": "red"}


def test_service_extras_is_the_passthrough_for_any_other_spec() -> None:
    extras = service_extras(
        _spec(_close),
        _SENT,
        {"colour": "red"},
        argument_binding=ArgumentBinding.AUTO,
        reserved=RESERVED_POOL_SEEDS,
    )

    assert extras == {"colour": "red"}
