"""On the kernel route, an author callable's DRF refusal leaves as the kernel's class.

``dispatch(kernel_spec(spec))`` runs what the compile hands the kernel: the
service or selector, its preconditions, its ``kwargs`` and ``input_data``
providers, ``extend_queryset``, and the permission classes through
``DRFPermissionCheck``. Each refusal DRF names is answered as the kernel's:

- ``ValidationError`` as the kernel's ``ServiceValidationError``, with DRF's
  detail and codes, still DRF's class;
- ``PermissionDenied`` as the kernel's ``NotPermitted``, with the class's
  message and code;
- ``NotAuthenticated`` as the kernel's ``NotPermitted``, still DRF's
  ``NotAuthenticated``, so a DRF handler answering it still answers 401;
- a raise already of the kernel's class leaves exactly as raised.

Asserted against the kernel's classes and DRF's alone, so each test runs on a
tree with no translator and fails there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.dispatch.adispatch import adispatch
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from django_service_specs.services.service_validation_error import (
    ServiceValidationError as KernelServiceValidationError,
)
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny, BasePermission

from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.not_permitted import NotPermitted
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

_OPEN: Any = [AllowAny]
# ``extend_queryset`` takes drfs' positional ``view`` and ``request``, which only
# the edge seeds.
_SEEDS = DEFAULT_POOL_SEEDS.extend(view=lambda: None, request=lambda: None)


def _run(spec: Any, arguments: dict[str, Any] | None = None) -> Any:
    return dispatch(
        kernel_spec(spec), principal=AnonymousUser(), arguments=arguments or {}, pool_seeds=_SEEDS
    )


def _arun(spec: Any, arguments: dict[str, Any] | None = None) -> Any:
    return async_to_sync(adispatch)(
        kernel_spec(spec), principal=AnonymousUser(), arguments=arguments or {}, pool_seeds=_SEEDS
    )


_ROUTES = pytest.mark.parametrize("run", [_run, _arun], ids=["sync", "async"])


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()


def _raising(error: Exception) -> Callable[..., Any]:
    def refuse(**_: Any) -> Any:
        raise error

    return refuse


def _araising(error: Exception) -> Callable[..., Any]:
    async def refuse(**_: Any) -> Any:
        raise error

    return refuse


def _rows(**_: Any) -> QuerySet[Post]:
    return Post.objects.all()


def _service_spec(seat: str, refuse: Callable[..., Any]) -> ServiceSpec[Any, Any, Any]:
    """A service spec whose ``seat`` refuses, every other seat passing."""
    if seat == "service":
        return ServiceSpec(permission_classes=_OPEN, service=refuse)
    if seat == "precondition":
        return ServiceSpec(
            permission_classes=_OPEN, service=lambda **_: None, preconditions=[refuse]
        )
    if seat == "kwargs":
        return ServiceSpec(permission_classes=_OPEN, service=lambda **_: None, kwargs=refuse)
    if seat == "input_data":
        return ServiceSpec(
            permission_classes=_OPEN,
            service=lambda **_: None,
            input_serializer=_TitleInput,
            input_data=refuse,
        )
    assert seat == "output_extend_queryset"
    return ServiceSpec(
        permission_classes=_OPEN,
        service=lambda **_: Post.objects.create(title="t"),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, instance: Post.objects.filter(pk=instance.pk),
            extend_queryset=_extending_from(refuse),
        ),
    )


def _extending_from(refuse: Callable[..., Any]) -> Callable[..., Any]:
    def extend(queryset: Any, view: Any, request: Any) -> Any:
        return refuse()

    return extend


def _selector_spec(seat: str, refuse: Callable[..., Any]) -> SelectorSpec[Any, Any]:
    if seat == "selector":
        return SelectorSpec(permission_classes=_OPEN, kind=SelectorKind.LIST, selector=refuse)
    if seat == "selector_precondition":
        return SelectorSpec(
            permission_classes=_OPEN, kind=SelectorKind.LIST, selector=_rows, preconditions=[refuse]
        )
    if seat == "selector_kwargs":
        return SelectorSpec(
            permission_classes=_OPEN, kind=SelectorKind.LIST, selector=_rows, kwargs=refuse
        )
    assert seat == "extend_queryset"
    return SelectorSpec(
        permission_classes=_OPEN,
        kind=SelectorKind.LIST,
        selector=_rows,
        extend_queryset=_extending_from(refuse),
    )


_SERVICE_SEATS = ["service", "precondition", "kwargs", "input_data", "output_extend_queryset"]
_SELECTOR_SEATS = ["selector", "selector_precondition", "selector_kwargs", "extend_queryset"]


_SEATS = pytest.mark.parametrize("seat", _SERVICE_SEATS + _SELECTOR_SEATS)


def _spec(seat: str, refuse: Callable[..., Any]) -> Any:
    return _service_spec(seat, refuse) if seat in _SERVICE_SEATS else _selector_spec(seat, refuse)


@_ROUTES
@_SEATS
def test_a_validation_error_leaves_as_the_kernel_s_service_validation_error(
    run: Any, seat: str
) -> None:
    error = ValidationError({"title": ["Too long."]}, code="too_long")

    with pytest.raises(KernelServiceValidationError) as excinfo:
        run(_spec(seat, _raising(error)), {"title": "x"} if seat == "input_data" else None)

    refusal = excinfo.value
    # Still DRF's class, so an ``except`` written against DRF's still catches it,
    # with DRF's detail and its codes.
    assert isinstance(refusal, ValidationError)
    assert refusal.detail == {"title": ["Too long."]}
    assert refusal.get_codes() == {"title": ["too_long"]}
    assert refusal.status_code == 400
    assert refusal.__cause__ is error


@_ROUTES
def test_a_message_detail_is_kept_as_drf_gave_it(run: Any) -> None:
    with pytest.raises(KernelServiceValidationError) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(ValidationError("whole"))))

    assert excinfo.value.detail == ["whole"]
    assert excinfo.value.get_codes() == ["invalid"]  # ty: ignore[unresolved-attribute]


@_ROUTES
def test_an_async_service_s_refusal_is_translated_too(run: Any) -> None:
    with pytest.raises(KernelServiceValidationError):
        run(ServiceSpec(permission_classes=_OPEN, service=_araising(ValidationError("whole"))))


@_ROUTES
def test_an_async_selector_s_refusal_is_translated_too(run: Any) -> None:
    with pytest.raises(KernelNotPermitted):
        run(
            SelectorSpec(
                permission_classes=_OPEN,
                kind=SelectorKind.LIST,
                selector=_araising(PermissionDenied("nope")),
            )
        )


@_ROUTES
@_SEATS
def test_a_permission_denied_leaves_as_the_kernel_s_not_permitted(run: Any, seat: str) -> None:
    error = PermissionDenied("Archived posts are read-only.", code="archived")

    with pytest.raises(KernelNotPermitted) as excinfo:
        run(_spec(seat, _raising(error)), {"title": "x"} if seat == "input_data" else None)

    refusal = excinfo.value
    assert refusal.message == "Archived posts are read-only."
    assert isinstance(refusal, PermissionDenied)
    assert refusal.get_codes() == "archived"
    assert refusal.status_code == 403
    assert refusal.__cause__ is error


@_ROUTES
@_SEATS
def test_not_authenticated_leaves_as_the_kernel_s_not_permitted_and_still_answers_401(
    run: Any, seat: str
) -> None:
    error = NotAuthenticated()

    with pytest.raises(KernelNotPermitted) as excinfo:
        run(_spec(seat, _raising(error)), {"title": "x"} if seat == "input_data" else None)

    refusal = excinfo.value
    assert isinstance(refusal, NotAuthenticated)
    assert refusal.message == "Authentication credentials were not provided."
    assert refusal.get_codes() == "not_authenticated"
    assert refusal.status_code == 401


class _Locked(ValidationError):
    """A project's own refusal, answered with a status of its own."""

    status_code = 423


@_ROUTES
def test_a_project_subclass_keeps_its_status(run: Any) -> None:
    with pytest.raises(KernelServiceValidationError) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(_Locked("locked"))))

    assert excinfo.value.status_code == 423
    assert excinfo.value.detail == ["locked"]


class _Gone(PermissionDenied):
    status_code = 410


@_ROUTES
def test_a_permission_subclass_keeps_its_status(run: Any) -> None:
    with pytest.raises(KernelNotPermitted) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(_Gone("gone"))))

    assert excinfo.value.status_code == 410


@_ROUTES
def test_a_permission_denied_with_a_structured_detail_keeps_it(run: Any) -> None:
    """A detail that is no message keeps its shape, and the message is the kernel's default."""
    error = PermissionDenied({"post": ["Not yours."]})

    with pytest.raises(KernelNotPermitted) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(error)))

    assert excinfo.value.detail == {"post": ["Not yours."]}
    assert excinfo.value.message == KernelNotPermitted.default_message


class _BothValidationErrors(KernelServiceValidationError, ValidationError):
    """DRF's ``ValidationError`` that is the kernel's service error too, as a translated one is."""

    def __init__(self, detail: Any) -> None:
        KernelServiceValidationError.__init__(self, detail)
        ValidationError.__init__(self, detail)


@_ROUTES
@pytest.mark.parametrize(
    "error",
    [
        KernelNotPermitted("kernel's own"),
        KernelServiceValidationError({"title": ["kernel's own"]}),
        # DRF's classes as well, so only the check for the kernel's keeps them.
        NotPermitted("drfs' dual class"),
        InvalidArguments({"title": ["drfs' dual class"]}),
        _BothValidationErrors({"title": ["a twin, translated by an inner callable"]}),
    ],
    ids=["dispatch-error", "service-error", "dual-not-permitted", "dual-invalid", "twin"],
)
def test_a_kernel_refusal_leaves_exactly_as_raised(run: Any, error: Exception) -> None:
    with pytest.raises(type(error)) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(error)))

    assert excinfo.value is error


@_ROUTES
@pytest.mark.parametrize(
    "error",
    [LookupError("not a refusal"), NotFound("No such post.")],
    ids=["not-drf-s", "drf-s-with-no-kernel-class"],
)
def test_any_other_exception_leaves_exactly_as_raised(run: Any, error: Exception) -> None:

    with pytest.raises(type(error)) as excinfo:
        run(ServiceSpec(permission_classes=_OPEN, service=_raising(error)))

    assert excinfo.value is error


class _Refusing(BasePermission):
    raises: Exception

    def has_permission(self, request: Any, view: Any) -> bool:
        raise self.raises


class _RefusingTheRow(BasePermission):
    raises: Exception

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        raise self.raises


def _guarded(permission: type[BasePermission], error: Exception) -> Any:
    refusing = type("Refusing", (permission,), {"raises": error})
    post = Post.objects.create(title="t")
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.get(pk=post.pk),
        permission_classes=[refusing],
    )


@_ROUTES
@pytest.mark.parametrize("permission", [_Refusing, _RefusingTheRow], ids=["class", "object"])
def test_a_permission_class_raising_permission_denied_leaves_as_not_permitted(
    run: Any, permission: type[BasePermission]
) -> None:
    error = PermissionDenied("nope", code="no_entry")

    with pytest.raises(KernelNotPermitted) as excinfo:
        run(_guarded(permission, error))

    assert excinfo.value.message == "nope"
    assert excinfo.value.get_codes() == "no_entry"  # ty: ignore[unresolved-attribute]
    assert excinfo.value.__cause__ is error


@_ROUTES
@pytest.mark.parametrize("permission", [_Refusing, _RefusingTheRow], ids=["class", "object"])
def test_a_permission_class_raising_not_authenticated_leaves_as_not_permitted(
    run: Any, permission: type[BasePermission]
) -> None:
    with pytest.raises(KernelNotPermitted) as excinfo:
        run(_guarded(permission, NotAuthenticated()))

    assert isinstance(excinfo.value, NotAuthenticated)
    assert excinfo.value.message == "Authentication credentials were not provided."
    assert excinfo.value.get_codes() == "not_authenticated"  # ty: ignore[unresolved-attribute]
    assert excinfo.value.status_code == 401  # ty: ignore[unresolved-attribute]


@_ROUTES
def test_the_input_serializer_s_context_provider_still_raises_drf_s_class(run: Any) -> None:
    """It runs inside the Validator, which the compile does not wrap."""
    error = ValidationError("not translated")
    spec = ServiceSpec(
        permission_classes=_OPEN,
        service=lambda **_: None,
        input_serializer=_TitleInput,
        input_serializer_context=_raising(error),
    )

    with pytest.raises(ValidationError) as excinfo:
        run(spec, {"title": "x"})

    assert excinfo.value is error
