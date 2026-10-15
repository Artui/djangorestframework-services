"""A key a precondition marks ``NotClientInput`` is the server's for the whole call.

The selector or service names ``tenant`` plainly while its precondition hides it,
so the caller's value reaches neither, ``REJECT`` refuses it as unexpected, and
the input schema does not list it: three statements of one rule.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
    spec_to_json_schema,
)


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]


def _gate(*, tenant: Annotated[str, NotClientInput] = "own") -> None:
    assert tenant == "own"


def _scoped_report(*, tenant: str = "default") -> str:
    return tenant


def _scoped_close(*, reason: str, tenant: str = "default") -> dict[str, str]:
    return {"reason": reason, "tenant": tenant}


@pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("spec", "binding", "params", "delivered"),
    [
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_scoped_report, preconditions=[_gate]
            ),
            ArgumentBinding.AUTO,
            {"tenant": "evil"},
            "default",
            id="selector",
        ),
        pytest.param(
            ServiceSpec(service=_scoped_close, preconditions=[_gate], atomic=False),
            ArgumentBinding.SPREAD_AUTHOR_WINS,
            {"reason": "r", "tenant": "evil"},
            {"reason": "r", "tenant": "default"},
            id="spread-service",
        ),
    ],
)
def test_a_key_a_precondition_hides_is_server_owned_for_the_whole_call(
    dispatch: Dispatch,
    spec: Any,
    binding: ArgumentBinding,
    params: dict[str, Any],
    delivered: Any,
) -> None:
    assert dispatch(spec, params=params, argument_binding=binding).value == delivered

    with pytest.raises(Exception) as excinfo:
        dispatch(
            spec,
            params=params,
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
        )
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}

    schema = spec_to_json_schema(spec, argument_binding=binding)
    assert "tenant" not in schema.get("properties", {})
