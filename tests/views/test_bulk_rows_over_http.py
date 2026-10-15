"""A live view's list body is refused by its list serializer, in the shape the installed DRF gives.

DRF's ``ListSerializer`` reports its rows' refusals as a list with an entry per
row below 3.18, and from 3.18 as a mapping of the failing rows' indexes; a row
that is no object is refused in its own words, and a list-level ``validate()``
under ``non_field_errors``. A view always answered with ``serializer.errors``
as they came, so each expectation here is what a bare DRF list serializer
answers for the same body, on whichever DRF is installed, bound with the
request in its context as a view binds it.

Off HTTP the kernel's refusal stays: its shape check's wording for a row that
is no object, laid out with an entry per row.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec
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


class _Rows(serializers.ListSerializer):
    def validate(self, attrs: Any) -> Any:
        if any(row["title"] == "dup" for row in attrs[1:]) and attrs[0]["title"] == "dup":
            raise serializers.ValidationError("Titles repeat.")
        return attrs


class _Row(serializers.Serializer):
    title = serializers.CharField(max_length=5)
    # Needs the request in the context: a row bound without it raises instead of refusing.
    owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        list_serializer_class = _Rows


def _rows(*, data: Any) -> None:
    SEEN["data"] = [dict(row) for row in data]


_SPEC = ServiceSpec(service=_rows, input_serializer=_Row, many=True, atomic=False)


class _CreateView(ServiceCreateView):
    spec = _SPEC


class _UpdateView(ServiceUpdateView):
    spec = _SPEC


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


def _drf_answer(body: Any) -> Any:
    """What a bare DRF list serializer answers for the body, bound as a view binds it."""
    request = Request(_factory.post("/", body, format="json"), parsers=[JSONParser()])
    request.user = AnonymousUser()
    bound = _Row(data=request.data, many=True, context={"request": request})
    assert not bound.is_valid()
    return bound.errors


_REFUSED = {
    "a-field-in-one-row": [{"title": "ok"}, {"title": "toolong"}, {"title": "fine"}],
    "a-field-in-two-rows": [{"title": "toolong"}, {"title": "ok"}, {}],
    "a-row-that-is-no-object": [{"title": "ok"}, 5],
    "a-row-that-is-no-object-beside-a-refused-one": [{"title": "toolong"}, "x"],
    "the-list-itself": [{"title": "dup"}, {"title": "dup"}],
}


@pytest.mark.django_db
@pytest.mark.parametrize("body", _REFUSED.values(), ids=_REFUSED.keys())
@pytest.mark.parametrize(
    ("view", "method"),
    [(_CreateView, "post"), (_UpdateView, "put"), (_UpdateView, "patch")],
    ids=["create", "update-put", "update-patch"],
)
def test_a_list_body_is_refused_as_its_list_serializer_refuses_it(
    view: Any, method: str, body: Any
) -> None:
    response = view.as_view()(getattr(_factory, method)("/", body, format="json"))

    assert response.status_code == 400
    # A bulk spec's rows are bound as the spec declares them, a PATCH's included.
    assert response.data == _drf_answer(body)
    assert "data" not in SEEN


@pytest.mark.django_db
def test_a_row_that_is_no_object_is_refused_in_drfs_words() -> None:
    """Pinned outright, so the comparison above cannot agree with the kernel's wording."""
    response = _CreateView.as_view()(_factory.post("/", [{"title": "ok"}, 5], format="json"))

    assert "Invalid data. Expected a dictionary, but got int." in str(response.data)
    assert "Expected an object." not in str(response.data)


@pytest.mark.django_db
def test_a_list_level_refusal_is_answered() -> None:
    """Pinned outright: the list's own message is no row's, and once was lost."""
    response = _CreateView.as_view()(
        _factory.post("/", [{"title": "dup"}, {"title": "dup"}], format="json")
    )

    assert response.data == {"non_field_errors": ["Titles repeat."]}


def _server_title() -> dict[str, Any]:
    return {"title": "srv"}


class _ServerInputView(ServiceCreateView):
    spec = ServiceSpec(
        service=_rows, input_serializer=_Row, many=True, input_data=_server_title, atomic=False
    )


@pytest.mark.django_db
def test_a_row_that_is_no_object_beside_an_input_data_provider_is_refused() -> None:
    """The provider's keys go over each row that is an object, and none other."""
    response = _ServerInputView.as_view()(
        _factory.post("/", [{"title": "toolong"}, 5], format="json")
    )

    assert response.status_code == 400
    assert response.data == _drf_answer([{"title": "srv"}, 5])
    assert "data" not in SEEN


_NO_ROWS = {
    "a-list-for-the-arguments": ([{"title": "a"}], {"non_field_errors": ["Expected an object."]}),
    "no-list-under-the-argument": ({"items": "x"}, {"items": ["Expected an array."]}),
}


@pytest.mark.parametrize(("params", "expected"), _NO_ROWS.values(), ids=_NO_ROWS.keys())
def test_rows_under_an_argument_beside_an_input_data_provider_keep_the_kernels_refusal(
    params: Any, expected: Any
) -> None:
    """With no list of rows sent, the provider's keys have nothing to go over."""
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_ServerInputView.spec, user=None, params=params, many_as_argument=True)

    assert excinfo.value.detail == expected


@pytest.mark.django_db
def test_valid_rows_still_reach_the_service() -> None:
    response = _CreateView.as_view()(
        _factory.post("/", [{"title": "a"}, {"title": "b"}], format="json")
    )

    assert response.status_code == 201, response.data
    assert [row["title"] for row in SEEN["data"]] == ["a", "b"]


def _live_view(body: Any) -> _CreateView:
    view = _CreateView()
    view.request = Request(_factory.post("/", body, format="json"), parsers=[JSONParser()])
    view.request.user = AnonymousUser()
    view.kwargs = {}
    view.format_kwarg = None
    return view


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("body", _REFUSED.values(), ids=_REFUSED.keys())
async def test_a_list_body_is_refused_alike_on_either_core(body: Any) -> None:
    def call_sync() -> Any:
        view = _live_view(body)
        with pytest.raises(ValidationError) as excinfo:
            dispatch_spec(_SPEC, user=None, params=body, request=view.request, view=view)
        return excinfo.value.detail

    sync_detail = await sync_to_async(call_sync, thread_sensitive=True)()
    view = await sync_to_async(_live_view, thread_sensitive=True)(body)
    with pytest.raises(ValidationError) as excinfo:
        await adispatch_spec(_SPEC, user=None, params=body, request=view.request, view=view)

    assert sync_detail == excinfo.value.detail
    assert sync_detail == await sync_to_async(_drf_answer, thread_sensitive=True)(body)


@pytest.mark.django_db
def test_off_http_a_list_level_refusal_sits_under_non_field_errors() -> None:
    view = OfflineServiceView(request=Request(_factory.post("/")))
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _SPEC,
            user=None,
            params=[{"title": "dup"}, {"title": "dup"}],
            request=view.request,
            view=view,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Titles repeat."]}


@pytest.mark.django_db
def test_off_http_a_refused_row_keeps_the_kernels_shape() -> None:
    view = OfflineServiceView(request=Request(_factory.post("/")))
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _SPEC,
            user=None,
            params=[{"title": "ok"}, 5, {"title": "toolong"}],
            request=view.request,
            view=view,
        )

    detail = excinfo.value.detail
    assert isinstance(detail, list)
    assert detail[:2] == [{}, {"non_field_errors": ["Expected an object."]}]
    assert list(detail[2]) == ["title"]


_UNDER_AN_ARGUMENT = {
    "a-row-that-is-no-object": (
        {"items": [{"title": "ok"}, 5]},
        {"items": {1: {"non_field_errors": ["Expected an object."]}}},
    ),
    "a-list-for-the-arguments": (
        [{"title": "ok"}],
        {"non_field_errors": ["Expected an object."]},
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "expected"), _UNDER_AN_ARGUMENT.values(), ids=_UNDER_AN_ARGUMENT.keys()
)
def test_a_live_views_rows_under_an_argument_keep_the_kernels_refusal(
    params: Any, expected: Any
) -> None:
    """Rows sent under an argument are no body DRF's list serializer was handed."""
    view = _live_view(params)
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _SPEC,
            user=None,
            params=params,
            request=view.request,
            view=view,
            many_as_argument=True,
        )

    assert excinfo.value.detail == expected


class _InnerRow(serializers.Serializer):
    title = serializers.CharField()


def _inner_rows(*, data: Any) -> None:
    SEEN["inner"] = [dict(row) for row in data]


_INNER = ServiceSpec(service=_inner_rows, input_serializer=_InnerRow, many=True, atomic=False)


def _calls_the_kernel(*, data: Any) -> None:
    principal = AnonymousUser()
    compiled = kernel_spec(_INNER)
    dispatch(
        compiled,
        principal=principal,
        arguments={"items": [{"title": "inner"}]},
        grant=Grant(compiled, principal),
    )


class _OuterView(ServiceCreateView):
    spec = ServiceSpec(service=_calls_the_kernel, input_serializer=_Row, many=True, atomic=False)


@pytest.mark.django_db
def test_a_kernel_call_inside_a_bulk_service_binds_its_own_rows() -> None:
    response = _OuterView.as_view()(_factory.post("/", [{"title": "outer"}], format="json"))

    assert response.status_code == 201, response.data
    assert SEEN["inner"] == [{"title": "inner"}]
