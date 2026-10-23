"""Over HTTP and through ``dispatch_spec``, an author's DRF refusal leaves exactly as it was raised.

The compile hands the kernel each author callable with its DRF refusals
translated into the kernel's classes, so the kernel route reads them. The
edge runs the same compiled callables, and restores the exception the author
raised before any of its own arms reads it. So every route that answered on
0.61.0 answers the same: the status and body of a live view, byte for byte,
and the class, detail and status a ``dispatch_spec`` caller catches.

The replays below were recorded against 0.61.0 and pass there unchanged. The
class checks are what fail with the restore removed; a 403 body would match
without it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from rest_framework import serializers
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.service_specs.not_permitted import NotPermitted
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _Headline(serializers.Serializer):
    headline = serializers.CharField(source="title")


def _raising(error: Exception) -> Callable[..., Any]:
    def refuse(**_: Any) -> Any:
        raise error

    return refuse


def _rows(**_: Any) -> QuerySet[Post]:
    return Post.objects.all()


def _service_spec(seat: str, error: Exception) -> ServiceSpec[Any, Any, Any]:
    refuse = _raising(error)
    if seat == "service":
        return ServiceSpec(service=refuse, input_serializer=_Headline)
    if seat == "precondition":
        return ServiceSpec(
            service=lambda **_: None, input_serializer=_Headline, preconditions=[refuse]
        )
    assert seat == "kwargs"
    return ServiceSpec(service=lambda **_: None, input_serializer=_Headline, kwargs=refuse)


_SERVICE_SEATS = pytest.mark.parametrize("seat", ["service", "precondition", "kwargs"])


@_CORES
@_SERVICE_SEATS
def test_a_validation_error_is_drf_s_keyed_by_the_wire_names(dispatch: Any, seat: str) -> None:
    error = ValidationError({"title": ["Too long."]})

    with pytest.raises(ValidationError) as excinfo:
        dispatch(_service_spec(seat, error), params={"headline": "x"})

    assert type(excinfo.value) is ValidationError
    assert excinfo.value.detail == {"headline": ["Too long."]}


@_CORES
@_SERVICE_SEATS
@pytest.mark.parametrize(
    "error",
    [PermissionDenied("Archived posts are read-only."), NotAuthenticated()],
    ids=["permission-denied", "not-authenticated"],
)
def test_a_denial_is_the_exception_the_author_raised(
    dispatch: Any, seat: str, error: Exception
) -> None:
    with pytest.raises(type(error)) as excinfo:
        dispatch(_service_spec(seat, error), params={"headline": "x"})

    assert excinfo.value is error


@_CORES
@pytest.mark.parametrize(
    "error",
    [ValidationError("whole"), PermissionDenied("nope"), NotAuthenticated()],
    ids=["validation-error", "permission-denied", "not-authenticated"],
)
@pytest.mark.parametrize("seat", ["selector", "precondition", "kwargs", "extend_queryset"])
def test_a_selector_spec_s_refusal_is_the_exception_the_author_raised(
    dispatch: Any, error: Exception, seat: str
) -> None:
    refuse = _raising(error)

    def extend(queryset: Any, view: Any, request: Any) -> Any:
        raise error

    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=refuse if seat == "selector" else _rows,
        preconditions=[refuse] if seat == "precondition" else None,
        kwargs=refuse if seat == "kwargs" else None,
        extend_queryset=extend if seat == "extend_queryset" else None,
    )

    with pytest.raises(type(error)) as excinfo:
        dispatch(spec, params={})

    assert excinfo.value is error


class _Locked(ValidationError):
    status_code = 423


@_CORES
def test_a_project_subclass_with_its_own_status_is_the_exception_raised(dispatch: Any) -> None:
    """With no input serializer there is nothing to rename, so the raise leaves as it was."""
    error = _Locked("locked")

    with pytest.raises(_Locked) as excinfo:
        dispatch(ServiceSpec(service=_raising(error)), params={})

    assert excinfo.value is error
    assert excinfo.value.status_code == 423


@_CORES
def test_the_exception_keeps_the_context_it_was_raised_in(dispatch: Any) -> None:
    def service(**_: Any) -> None:
        try:
            {}["missing"]
        except KeyError:
            raise ValidationError("whole") from None

    with pytest.raises(ValidationError) as excinfo:
        dispatch(ServiceSpec(service=service), params={})

    assert isinstance(excinfo.value.__context__, KeyError)
    assert excinfo.value.__suppress_context__ is True


@_CORES
def test_a_raise_with_no_context_is_restored_with_none(dispatch: Any) -> None:
    with pytest.raises(PermissionDenied) as excinfo:
        dispatch(ServiceSpec(service=_raising(PermissionDenied("nope"))), params={})

    assert excinfo.value.__context__ is None


@_CORES
def test_the_kernel_s_denial_still_leaves_as_drfs_not_permitted(dispatch: Any) -> None:
    """Translated by ``translate_dispatch_errors``, which the restore leaves alone."""
    with pytest.raises(NotPermitted) as excinfo:
        dispatch(ServiceSpec(service=_raising(KernelNotPermitted("kernel's"))), params={})

    assert type(excinfo.value) is NotPermitted
    assert str(excinfo.value.detail) == "kernel's"


@_CORES
def test_drfs_own_not_permitted_leaves_exactly_as_raised(dispatch: Any) -> None:
    error = NotPermitted("drfs' own")

    with pytest.raises(NotPermitted) as excinfo:
        dispatch(ServiceSpec(service=_raising(error)), params={})

    assert excinfo.value is error


# --- Over HTTP, as 0.61.0 answered -------------------------------------------


def _served(view: Any, method: str = "post", data: Any = None, **headers: Any) -> Any:
    request = getattr(_factory, method)("/", data if data is not None else {}, **headers)
    response = view(request)
    response.render()
    return response


def _create(error: Exception, seat: str = "service", *, authenticated: bool = False) -> Any:
    class _View(ServiceCreateView):
        permission_classes: Any = []
        authentication_classes: Any = [BasicAuthentication] if authenticated else []
        spec = _service_spec(seat, error)

    return _View.as_view()


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        (ValidationError("whole"), 400, b'["whole"]'),
        (ValidationError({"title": ["bad"]}), 400, b'{"headline":["bad"]}'),
        (PermissionDenied("nope"), 403, b'{"detail":"nope"}'),
        (_Locked("locked"), 400, b'["locked"]'),
    ],
    ids=["message", "mapping", "permission-denied", "project-subclass"],
)
@_SERVICE_SEATS
def test_a_service_spec_s_refusal_answers_as_on_0_61_0(
    error: Exception, status: int, body: bytes, seat: str
) -> None:
    response = _served(_create(error, seat), data={"headline": "x"})

    assert (response.status_code, response.content) == (status, body)


@_SERVICE_SEATS
def test_not_authenticated_answers_401_with_its_challenge_as_on_0_61_0(seat: str) -> None:
    response = _served(
        _create(NotAuthenticated(), seat, authenticated=True), data={"headline": "x"}
    )

    assert response.status_code == 401
    assert response.content == b'{"detail":"Authentication credentials were not provided."}'
    assert response["WWW-Authenticate"] == 'Basic realm="api"'


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        (ValidationError("whole"), 400, b'["whole"]'),
        (ValidationError({"title": ["bad"]}), 400, b'{"title":["bad"]}'),
        (PermissionDenied("nope"), 403, b'{"detail":"nope"}'),
    ],
    ids=["message", "mapping", "permission-denied"],
)
@pytest.mark.parametrize("seat", ["selector", "precondition", "kwargs"])
def test_a_selector_list_view_s_refusal_answers_as_on_0_61_0(
    error: Exception, status: int, body: bytes, seat: str
) -> None:
    refuse = _raising(error)

    class _View(SelectorListView):
        permission_classes: Any = []
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=refuse if seat == "selector" else _rows,
            preconditions=[refuse] if seat == "precondition" else None,
            kwargs=refuse if seat == "kwargs" else None,
        )

    response = _served(_View.as_view(), "get")

    assert (response.status_code, response.content) == (status, body)


class _Unauthenticated(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        raise NotAuthenticated()


class _Refusing(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        raise PermissionDenied("nope")


@pytest.mark.parametrize(
    ("permission", "status", "body", "challenge"),
    [
        (
            _Unauthenticated,
            401,
            b'{"detail":"Authentication credentials were not provided."}',
            'Basic realm="api"',
        ),
        (_Refusing, 403, b'{"detail":"nope"}', None),
    ],
    ids=["not-authenticated", "permission-denied"],
)
def test_a_permission_class_s_raised_refusal_answers_as_on_0_61_0(
    permission: type[BasePermission], status: int, body: bytes, challenge: str | None
) -> None:
    post = Post.objects.create(title="t")

    class _View(SelectorRetrieveView):
        authentication_classes: Any = [BasicAuthentication]
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: Post.objects.get(pk=post.pk),
            permission_classes=[permission],
        )

    response = _served(_View.as_view(), "get")

    assert (response.status_code, response.content) == (status, body)
    assert response.get("WWW-Authenticate") == challenge


@_CORES
@pytest.mark.parametrize(
    "error",
    [ValidationError("whole"), PermissionDenied("nope"), NotAuthenticated()],
    ids=["validation-error", "permission-denied", "not-authenticated"],
)
@pytest.mark.parametrize("seat", ["instance_lookup", "output_selector"])
def test_a_nested_selector_s_refusal_is_the_exception_the_author_raised(
    dispatch: Any, error: Exception, seat: str
) -> None:
    nested = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_raising(error))
    spec = (
        ServiceSpec(service=lambda **_: None, instance_selector_spec=nested)
        if seat == "instance_lookup"
        else ServiceSpec(
            service=lambda **_: Post.objects.create(title="t"), output_selector_spec=nested
        )
    )

    with pytest.raises(type(error)) as excinfo:
        dispatch(spec, params={})

    assert excinfo.value is error
