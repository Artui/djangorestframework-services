"""Over HTTP, a view's input is answered by DRF's own serializer, as it always was.

The kernel checks a call's arguments against the Parameters a spec declares
before its Validator runs, which is right for a caller that sends typed JSON
(an agent transport, ``dispatch_spec`` called off HTTP) and wrong for a
browser or a form: DRF's fields take ``"true"`` and ``1`` for a boolean, ``123``
for a string, an HTML checkbox's ``"on"``, an empty string for an optional
field in a form, and an unchecked box's absence as ``False``, and DRF answers
every field's refusal at once in its own words. So a request a live DRF view
dispatches is validated by the serializer alone, from the body the request
parsed - a ``QueryDict`` kept a ``QueryDict`` - and each expectation here is
what a bare DRF serializer answers for the same body, on whichever DRF is
installed.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict

from rest_framework_services import ServiceCreateView, ServiceSpec, dispatch_spec
from rest_framework_services.types.offline_service_view import OfflineServiceView
from rest_framework_services.types.unset import UNSET

_factory = APIRequestFactory()
SEEN: dict[str, Any] = {}


class _Lenient(serializers.Serializer):
    title = serializers.CharField(max_length=5, required=False)
    views = serializers.IntegerField(required=False)
    published = serializers.BooleanField(required=False)
    tags = serializers.ListField(child=serializers.IntegerField(), required=False)
    nick = serializers.CharField(required=False, allow_null=True)


class _Checkbox(serializers.Serializer):
    title = serializers.CharField()
    published = serializers.BooleanField()


def _echo(*, data: Any) -> None:
    SEEN["data"] = dict(data)


class _LenientView(ServiceCreateView):
    spec = ServiceSpec(service=_echo, input_serializer=_Lenient, atomic=False)


class _CheckboxView(ServiceCreateView):
    spec = ServiceSpec(service=_echo, input_serializer=_Checkbox, atomic=False)


def _rows(*, data: Any) -> None:
    SEEN["data"] = list(data)


class _BulkView(ServiceCreateView):
    spec = ServiceSpec(service=_rows, input_serializer=_Lenient, many=True, atomic=False)


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


def _request(body: Any, fmt: str | None) -> Any:
    return _factory.post("/", body, format=fmt) if fmt else _factory.post("/", body)


def _drf_answer(serializer: type[Any], body: Any, fmt: str | None, **kwargs: Any) -> Any:
    """What a bare DRF serializer answers for the body the same request parses to."""
    parsed = Request(
        _request(body, fmt), parsers=[JSONParser(), FormParser(), MultiPartParser()]
    ).data
    bound = serializer(data=parsed, **kwargs)
    if not bound.is_valid():
        return bound.errors
    data = bound.validated_data
    return [dict(row) for row in data] if isinstance(data, list) else dict(data)


_JSON_ACCEPTED = {
    "bool-from-true-string": {"published": "true"},
    "bool-from-1-string": {"published": "1"},
    "bool-from-1": {"published": 1},
    "bool-from-on": {"published": "on"},
    "char-from-int": {"title": 123},
    "int-from-string": {"views": "5"},
}


@pytest.mark.parametrize("body", _JSON_ACCEPTED.values(), ids=_JSON_ACCEPTED.keys())
def test_json_drf_accepts_is_accepted(body: dict[str, Any]) -> None:
    response = _LenientView.as_view()(_request(body, "json"))

    assert response.status_code == 204, response.data
    assert SEEN["data"] == _drf_answer(_Lenient, body, "json")


_JSON_REFUSED = {
    # Every field's refusal at once: a type refusal hides no other field's.
    "two-fields": {"title": "toolong", "views": "x"},
    "null": {"title": None},
    "not-a-list": {"tags": 1},
    "not-a-string": {"title": True},
}


@pytest.mark.parametrize("body", _JSON_REFUSED.values(), ids=_JSON_REFUSED.keys())
def test_json_drf_refuses_is_refused_in_drfs_words(body: dict[str, Any]) -> None:
    response = _LenientView.as_view()(_request(body, "json"))

    assert response.status_code == 400
    assert response.data == _drf_answer(_Lenient, body, "json")


def test_both_fields_refusals_are_answered_together() -> None:
    """Pinned outright, so the comparison above cannot agree with a lost refusal."""
    response = _LenientView.as_view()(_request({"title": "toolong", "views": "x"}, "json"))

    assert set(response.data) == {"title", "views"}


_FORMS = {
    "checkbox-on": ({"published": "on"}, "multipart"),
    "checkbox-yes": ({"published": "yes"}, "multipart"),
    "empty-optional-int": ({"views": ""}, "multipart"),
    "empty-optional-nullable": ({"nick": ""}, "multipart"),
    "absent-checkbox": ({"views": "5"}, "multipart"),
    "form-encoded-checkbox": ({"published": "on"}, None),
    "repeated-key": ({"tags": ["1", "2"]}, None),
}


@pytest.mark.parametrize(("body", "fmt"), _FORMS.values(), ids=_FORMS.keys())
def test_a_form_body_is_read_as_html_input(body: dict[str, Any], fmt: str | None) -> None:
    response = _LenientView.as_view()(_request(body, fmt))

    assert response.status_code == 204, response.data
    assert SEEN["data"] == _drf_answer(_Lenient, body, fmt)


def test_an_unchecked_required_checkbox_is_false() -> None:
    """A form leaves an unchecked box out, and DRF reads that absence as ``False``."""
    response = _CheckboxView.as_view()(_request({"title": "t"}, "multipart"))

    assert response.status_code == 204, response.data
    assert SEEN["data"] == {"title": "t", "published": False}


@pytest.mark.parametrize("body", [{"title": "ok"}, {}], ids=["object", "empty"])
def test_a_bulk_body_that_is_no_list_is_refused_in_drfs_words(body: Any) -> None:
    response = _BulkView.as_view()(_request(body, "json"))

    assert response.status_code == 400
    assert response.data == _drf_answer(_Lenient, body, "json", many=True)
    assert "Expected a list of items" in str(response.data)


def test_a_bulk_row_is_read_as_drf_reads_it() -> None:
    body = [{"published": "true", "title": 123}]

    response = _BulkView.as_view()(_request(body, "json"))

    assert response.status_code == 201, response.data
    assert [dict(row) for row in SEEN["data"]] == _drf_answer(_Lenient, body, "json", many=True)


def _server_tenant() -> dict[str, Any]:
    return {"title": "srv"}


class _ServerInputView(ServiceCreateView):
    spec = ServiceSpec(
        service=_echo, input_serializer=_Lenient, input_data=_server_tenant, atomic=False
    )


@pytest.mark.parametrize(
    ("body", "got"), [([{"title": "ok"}], "list"), ("x", "str")], ids=["list", "string"]
)
@pytest.mark.parametrize(
    "view", [_LenientView, _ServerInputView], ids=["plain", "with-an-input-data-provider"]
)
def test_a_single_call_body_that_is_no_object_is_refused_in_drfs_words(
    view: Any, body: Any, got: str
) -> None:
    """As a view refused it: DRF's words, and which spec takes a list.

    An ``input_data`` provider's keys have no object to go over, and the body
    is still refused rather than raising.
    """
    response = view.as_view()(_request(body, "json"))

    assert response.status_code == 400
    assert response.data == {
        "non_field_errors": [
            f"Invalid data. Expected a dictionary, but got {got}. "
            "Only a spec declaring many=True accepts a list payload."
        ]
    }
    assert "data" not in SEEN


def _no_input() -> None:
    return None


class _NoInputView(ServiceCreateView):
    spec = ServiceSpec(service=_no_input, atomic=False)


def test_a_body_with_no_serializer_to_answer_it_keeps_the_kernels_refusal() -> None:
    """With no input serializer there is no DRF answer to give."""
    response = _NoInputView.as_view()(_request([1], "json"))

    assert response.status_code == 400
    assert response.data == {"non_field_errors": ["Expected an object."]}


def test_a_bulk_row_refusal_is_drfs_own() -> None:
    """A list body is the shape DRF takes, so its rows are refused as they always were."""
    body = [{"views": "x"}]

    response = _BulkView.as_view()(_request(body, "json"))

    assert response.status_code == 400
    assert "A valid integer is required." in str(response.data)


def _offline_view() -> OfflineServiceView:
    return OfflineServiceView(request=Request(_factory.post("/")))


@pytest.mark.parametrize("view", [None, _offline_view], ids=["no-view", "offline-view"])
def test_an_offline_view_keeps_the_kernels_typed_check(view: Any) -> None:
    """An agent transport's caller sends typed JSON, and is held to the declared types."""
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _LenientView.spec,
            user=None,
            params={"published": "true", "views": "x"},
            view=view() if view is not None else None,
        )

    assert excinfo.value.detail == {
        "published": ["Expected a boolean."],
        "views": ["Expected an integer."],
    }


def test_a_live_views_rows_under_an_argument_keep_the_kernels_refusal() -> None:
    """Rows sent under an argument are no body DRF's list serializer was handed."""
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _BulkView.spec,
            user=None,
            params={"items": "x"},
            view=_BulkView(),
            many_as_argument=True,
        )

    assert excinfo.value.detail == {"items": ["Expected an array."]}


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


class _OwnsTheTitle(TypedDict):
    title: str


def _decline_the_title() -> _OwnsTheTitle:
    return {"title": UNSET}


class _DeclinedTitleView(ServiceCreateView):
    spec = ServiceSpec(
        service=_echo, input_serializer=_TitleIn, kwargs=_decline_the_title, atomic=False
    )


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_declined_owned_field_is_taken_from_the_body(fmt: str) -> None:
    """A field a typed provider is annotated to fill is never the caller's, declined or not."""
    response = _DeclinedTitleView.as_view()(_request({"title": "client"}, fmt))

    assert response.status_code == 400
    assert response.data == {"title": ["This field is required."]}


class _ServerTagsView(ServiceCreateView):
    spec = ServiceSpec(service=_echo, input_serializer=_Lenient, atomic=False)

    def get_input_data(self, request: Any) -> dict[str, Any]:
        return {"tags": [1, 2], "title": "srv"}


def test_input_data_reaches_a_form_body_as_a_list() -> None:
    """The server's keys are written onto a form body as ``input_data`` always was."""
    response = _ServerTagsView.as_view()(_request({"title": "x", "views": "5"}, "multipart"))

    assert response.status_code == 204, response.data
    assert SEEN["data"] == {"tags": [1, 2], "title": "srv", "views": 5, "published": False}


def _inner(*, data: Any) -> None:
    SEEN["inner"] = dict(data)


_INNER = ServiceSpec(service=_inner, input_serializer=_TitleIn, atomic=False)


def _calls_the_kernel(*, data: Any) -> None:
    principal = AnonymousUser()
    compiled = kernel_spec(_INNER)
    dispatch(
        compiled,
        principal=principal,
        arguments={"title": "inner"},
        grant=Grant(compiled, principal),
    )


class _OuterView(ServiceCreateView):
    spec = ServiceSpec(service=_calls_the_kernel, input_serializer=_TitleIn, atomic=False)


def test_a_kernel_call_inside_the_service_binds_its_own_arguments() -> None:
    response = _OuterView.as_view()(_request({"title": "outer"}, "json"))

    assert response.status_code == 204, response.data
    assert SEEN["inner"] == {"title": "inner"}
