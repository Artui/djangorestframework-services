"""A live view's bulk body is as long as its list serializer allows, as DRF's view always took it.

DRF's ``ListSerializer`` bounds a list only where it declares ``max_length``,
``min_length`` or ``allow_empty=False``, so a live view took a body of any
length its serializer did not bound, and refused one past a declared bound in
DRF's words. The kernel caps an array that declares no maximum at
``SERVICE_SPECS_MAX_ARRAY_LENGTH`` (1000 by default), which is for the agent
transports: off HTTP, and on an ``OfflineServiceView``, the cap stays.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.offline_service_view import OfflineServiceView

_factory = APIRequestFactory()
SEEN: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


class _Row(serializers.Serializer):
    title = serializers.CharField()


class _AtMostThree(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 3)
        super().__init__(*args, **kwargs)


class _BoundedRow(serializers.Serializer):
    title = serializers.CharField()

    class Meta:
        list_serializer_class = _AtMostThree


class _NotEmpty(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_empty", False)
        kwargs.setdefault("min_length", 2)
        super().__init__(*args, **kwargs)


class _AtLeastTwoRow(serializers.Serializer):
    title = serializers.CharField()

    class Meta:
        list_serializer_class = _NotEmpty


def _rows(*, data: Any) -> None:
    SEEN["count"] = len(data)


def _spec(row: Any = _Row) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=_rows, input_serializer=row, many=True, atomic=False)


def _view(base: type, row: Any = _Row) -> Any:
    return type("_View", (base,), {"spec": _spec(row), "permission_classes": []}).as_view()


_LONG = [{"title": str(index)} for index in range(1001)]
_METHODS = pytest.mark.parametrize(
    ("base", "method", "status"),
    [
        (ServiceCreateView, "post", 201),
        (ServiceUpdateView, "put", 200),
        (ServiceUpdateView, "patch", 200),
    ],
    ids=["create", "update-put", "update-patch"],
)


@pytest.mark.django_db
@_METHODS
def test_a_live_view_takes_a_body_past_the_kernels_cap(
    base: type, method: str, status: int
) -> None:
    response = _view(base)(getattr(_factory, method)("/", _LONG, format="json"))

    assert response.status_code == status, response.data
    assert SEEN == {"count": 1001}


def _drf_answer(row: Any, body: Any) -> Any:
    """What a bare DRF list serializer answers for the body."""
    request = Request(_factory.post("/", body, format="json"), parsers=[JSONParser()])
    request.user = AnonymousUser()
    bound = row(data=request.data, many=True, context={"request": request})
    assert not bound.is_valid()
    return bound.errors


@pytest.mark.django_db
@_METHODS
@pytest.mark.parametrize(
    ("row", "body"),
    [
        (_BoundedRow, [{"title": "a"}] * 4),
        (_AtLeastTwoRow, []),
        (_AtLeastTwoRow, [{"title": "a"}]),
    ],
    ids=["past-its-max-length", "empty-where-it-allows-none", "short-of-its-min-length"],
)
def test_a_list_serializers_own_bound_still_refuses_in_drfs_words(
    base: type, method: str, status: int, row: Any, body: Any
) -> None:
    response = _view(base, row)(getattr(_factory, method)("/", body, format="json"))

    assert response.status_code == 400
    assert response.data == _drf_answer(row, body)
    assert SEEN == {}


@pytest.mark.parametrize(
    "view", [None, OfflineServiceView(request=None)], ids=["no-view", "offline-view"]
)
@pytest.mark.parametrize("core", ["sync", "async"])
def test_off_http_the_kernels_cap_stays(view: Any, core: str) -> None:
    run = dispatch_spec if core == "sync" else async_to_sync(adispatch_spec)

    with pytest.raises(ValidationError) as excinfo:
        run(_spec(), user=None, params=_LONG, view=view)

    assert excinfo.value.detail == {
        "non_field_errors": ["Ensure this field has no more than 1000 elements."]
    }
    assert SEEN == {}
