"""``apply_input_data`` - the server's keys over a client payload."""

from __future__ import annotations

from django.http import QueryDict

from rest_framework_services.dispatch.apply_input_data import apply_input_data


def test_nothing_to_merge_returns_the_payload_itself() -> None:
    # The same object, so a form body reaches the serializer as itself.
    body = QueryDict("name=a")

    assert apply_input_data(body, {}) is body
