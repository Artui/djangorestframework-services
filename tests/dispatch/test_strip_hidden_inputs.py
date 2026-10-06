"""``strip_hidden_inputs`` and ``server_owned_keys`` — the caller-mapping half of ``NotClientInput``."""

from __future__ import annotations

from typing import Annotated, Any

from django.http import QueryDict
from typing_extensions import TypedDict, Unpack

from rest_framework_services import NotClientInput, SelectorKind, SelectorSpec, ServiceSpec
from rest_framework_services.dispatch.utils import server_owned_keys, strip_hidden_inputs


def _unmarked(*, team: str = "own-team") -> str:
    return team


def _marked(*, team: Annotated[str, NotClientInput] = "own-team", limit: int = 10) -> str:
    return team


class _Extras(TypedDict, total=False):
    team: Annotated[str, NotClientInput]
    limit: int


def _marked_in_extras(**extras: Unpack[_Extras]) -> Any:
    return extras


def _owned(fn: Any) -> frozenset[str]:
    """What a selector spec over ``fn`` alone owns: ``fn``'s share of the union."""
    return server_owned_keys(SelectorSpec(kind=SelectorKind.LIST, selector=fn))


def test_an_unmarked_callable_gets_the_mapping_back_unchanged() -> None:
    """Identity, not a copy: a form-encoded body stays a ``QueryDict``, whose
    values a dict copy would turn into one-element lists."""
    params = QueryDict("team=other-team&limit=3")

    assert strip_hidden_inputs(params, _owned(_unmarked)) is params


def test_only_the_marked_key_is_dropped() -> None:
    assert strip_hidden_inputs({"team": "other-team", "limit": 3}, _owned(_marked)) == {"limit": 3}


def test_a_key_marked_inside_an_unpacked_typed_dict_is_dropped() -> None:
    assert strip_hidden_inputs({"team": "other-team", "limit": 3}, _owned(_marked_in_extras)) == {
        "limit": 3
    }


def test_a_callers_view_is_dropped_for_any_callable() -> None:
    """No callable marks ``view``, and no caller supplies it either."""
    assert strip_hidden_inputs({"view": "spoofed", "team": "other-team"}, _owned(_unmarked)) == {
        "team": "other-team"
    }


def test_a_mapping_carrying_no_hidden_key_comes_back_unchanged() -> None:
    """Identity whenever there is nothing to drop, marked callable or not."""
    params = QueryDict("limit=3")

    assert strip_hidden_inputs(params, _owned(_marked)) is params


def test_a_key_the_server_supplied_is_kept() -> None:
    """``input_data`` wrote the value, so it is the server's, not the caller's."""
    params = {"team": "server-team", "limit": 3}

    assert strip_hidden_inputs(params, _owned(_marked), server_supplied={"team"}) is params


def test_only_the_server_supplied_key_is_kept() -> None:
    """The server supplying one hidden key does not let the caller's others through."""
    params = {"team": "server-team", "view": "spoofed", "limit": 3}

    assert strip_hidden_inputs(params, _owned(_marked), server_supplied={"team"}) == {
        "team": "server-team",
        "limit": 3,
    }


# --- the union over every callable in the call ---------------------------------------


def _gate(*, region: Annotated[str, NotClientInput] = "eu") -> None: ...


def _lookup(*, pk: int, tenant: Annotated[str, NotClientInput] = "own") -> Any: ...


def _update(*, instance: Any, team: Annotated[str, NotClientInput] = "own-team") -> Any: ...


def test_a_service_owns_what_its_service_preconditions_and_lookup_hide() -> None:
    spec = ServiceSpec(
        service=_update,
        preconditions=[_gate],
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
    )

    assert server_owned_keys(spec) == {"team", "region", "tenant", "view"}


def test_a_selector_owns_what_its_preconditions_hide() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_unmarked, preconditions=[_gate])

    assert server_owned_keys(spec) == {"region", "view"}


def test_a_spec_with_nothing_to_call_owns_only_view() -> None:
    """A selector spec with no ``selector``, and a lookup with none: ``view`` alone."""
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE)

    assert server_owned_keys(SelectorSpec(kind=SelectorKind.LIST)) == {"view"}
    assert server_owned_keys(ServiceSpec(service=_unmarked, instance_selector_spec=lookup)) == {
        "view"
    }
