"""``AdditionalInputRequired`` — a service saying what it still needs.

The transport-neutral half of an interactive operation. Everything about *how*
a given protocol asks the question belongs to that protocol's transport; what
lives here is a service being able to say it without importing one.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers

from rest_framework_services import (
    AdditionalInputRequired,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    dispatch_spec,
)


class _DeleteIn(serializers.Serializer):
    count = serializers.IntegerField()
    confirmed = serializers.BooleanField(required=False, default=False)


def _delete_rows(*, data: Any) -> dict[str, Any]:
    if data["count"] > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{data['count']} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    return {"deleted": data["count"]}


def _dispatch(**params: Any) -> Any:
    return dispatch_spec(
        ServiceSpec(service=_delete_rows, input_serializer=_DeleteIn, atomic=False),
        user=None,
        params=params,
    )


def test_it_carries_the_message_and_the_shape_of_what_is_missing() -> None:
    error = AdditionalInputRequired("Confirm first", schema={"confirmed": {"type": "boolean"}})
    assert str(error) == "Confirm first"
    assert error.schema == {"confirmed": {"type": "boolean"}}


def test_the_schema_is_optional() -> None:
    """A message alone is still useful — a transport that cannot render a form
    can show it."""
    assert AdditionalInputRequired("Confirm first").schema is None


def test_it_is_a_service_error() -> None:
    """Deliberate: a transport that has never heard of this still does
    something sensible — the operation could not be completed, and here is why.
    Transports that *can* ask catch it first and do better."""
    assert issubclass(AdditionalInputRequired, ServiceError)


def test_it_is_not_a_validation_error() -> None:
    """Different claim. A validation error says what you sent is wrong; this
    says the service got far enough to discover it needs something else —
    usually conditional on what it found, which is why it cannot be a required
    field on the serializer."""
    assert not issubclass(AdditionalInputRequired, ServiceValidationError)


def test_a_service_can_raise_it_mid_dispatch() -> None:
    with pytest.raises(AdditionalInputRequired) as caught:
        _dispatch(count=400)
    assert caught.value.schema == {"confirmed": {"type": "boolean"}}
    assert "400 rows match" in str(caught.value)


def test_the_answer_arrives_as_ordinary_input() -> None:
    """The reason the service's involvement ends at the raise: there is no
    callback to hold and no session to resume. Whatever the transport does to
    ask, the answer comes back through the parameters the service already
    declares."""
    result = _dispatch(count=400, confirmed=True)
    assert result.value == {"deleted": 400}


def test_a_service_that_needs_nothing_is_unaffected() -> None:
    result = _dispatch(count=1)
    assert result.value == {"deleted": 1}


def test_catching_service_error_still_catches_it() -> None:
    """The fallback a transport gets for free, and the reason ordering matters
    for one that wants to do better: a handler for ``ServiceError`` will
    swallow this unless it checks for this first."""
    with pytest.raises(ServiceError):
        _dispatch(count=400)
