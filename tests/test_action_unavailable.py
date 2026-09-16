"""``ActionUnavailable`` — a conflict with a stable name."""

from __future__ import annotations

import pytest

from rest_framework_services import ActionUnavailable, ServiceConflict, ServiceError


def test_carries_the_code_and_the_message() -> None:
    exc = ActionUnavailable("Order 7 has shipped.", code="order_shipped")
    assert exc.code == "order_shipped"
    assert exc.message == "Order 7 has shipped."
    assert str(exc) == "Order 7 has shipped."


def test_the_message_defaults_and_the_code_does_not() -> None:
    exc = ActionUnavailable(code="order_shipped")
    assert exc.message == "This action is not available right now."
    with pytest.raises(TypeError):
        ActionUnavailable("no code")  # type: ignore[call-arg]


def test_a_generic_handler_still_catches_it() -> None:
    """Matched specific-first by a transport that knows it, and as a conflict or a
    plain service error by one that does not."""
    with pytest.raises(ServiceConflict):
        raise ActionUnavailable(code="c")
    with pytest.raises(ServiceError):
        raise ActionUnavailable(code="c")
