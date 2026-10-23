"""A serializer refusing to render leaves the presenter as the kernel's ``InvalidArguments``.

The kernel's ``present`` and ``present_for_audience`` call the presenter, and
a kernel route reads the kernel's refusal of the arguments, never DRF's
``ValidationError``. So ``SerializerPresenter.presented`` answers a
serializer's refusal as drfs' dual ``InvalidArguments``, still DRF's class:

- a mapping detail passes as it is;
- a list detail, which is all DRF makes of a message, goes under the
  kernel's non-field key;
- each message keeps its code.

No HTTP or ``dispatch_spec`` route reaches the presenter, so a live view and
``render_spec_output`` answer as on 0.61.0. Asserted against the kernel's
class and DRF's, so each test of the change runs and fails on a tree without it.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.present import present
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.parameters.non_field_errors import NON_FIELD_ERRORS
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorRetrieveView, SelectorSpec
from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.serializer_presenter import SerializerPresenter
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db


def _refusing(error: Exception) -> type[serializers.Serializer]:
    class _Refusing(serializers.Serializer):
        title = serializers.CharField()

        def to_representation(self, instance: Any) -> Any:
            raise error

    return _Refusing


_DETAILS = pytest.mark.parametrize(
    ("error", "detail", "codes"),
    [
        (
            ValidationError({"fields": ["No such field: nope."]}, code="unknown_field"),
            {"fields": ["No such field: nope."]},
            {"fields": ["unknown_field"]},
        ),
        (
            ValidationError(["Cannot render."], code="unrenderable"),
            {NON_FIELD_ERRORS: ["Cannot render."]},
            {NON_FIELD_ERRORS: ["unrenderable"]},
        ),
        (
            ValidationError("Cannot render."),
            {NON_FIELD_ERRORS: ["Cannot render."]},
            {NON_FIELD_ERRORS: ["invalid"]},
        ),
    ],
    ids=["mapping", "list", "message"],
)


@_DETAILS
def test_present_refuses_as_the_kernel_s_invalid_arguments(
    error: ValidationError, detail: Any, codes: Any
) -> None:
    with pytest.raises(KernelInvalidArguments) as excinfo:
        SerializerPresenter(_refusing(error)).present(object())

    refusal = excinfo.value
    assert isinstance(refusal, ValidationError)
    assert refusal.detail == detail
    assert refusal.get_codes() == codes
    assert refusal.__cause__ is error


@_DETAILS
def test_the_kernel_s_present_refuses_as_the_kernel_s_invalid_arguments(
    error: ValidationError, detail: Any, codes: Any
) -> None:
    """``present`` hands the presenter the call's context: the other half of ``presented``."""
    post = Post.objects.create(title="t")
    spec = kernel_spec(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: Post.objects.get(pk=post.pk),
            output_serializer=_refusing(error),
            permission_classes=[AllowAny],
        )
    )
    result = dispatch(spec, principal=AnonymousUser(), arguments={})

    with pytest.raises(KernelInvalidArguments) as excinfo:
        present(spec, result)

    assert excinfo.value.detail == detail
    assert excinfo.value.get_codes() == codes  # ty: ignore[unresolved-attribute]


def test_the_kernel_s_own_refusal_leaves_exactly_as_raised() -> None:
    """A serializer raising the kernel's class, drfs' dual one here, is not rebuilt."""
    error = InvalidArguments({"fields": ["kept"]})

    with pytest.raises(InvalidArguments) as excinfo:
        SerializerPresenter(_refusing(error)).present(object())

    assert excinfo.value is error


def test_any_other_exception_leaves_exactly_as_raised() -> None:
    error = LookupError("not a refusal")

    with pytest.raises(LookupError) as excinfo:
        SerializerPresenter(_refusing(error)).present(object())

    assert excinfo.value is error


@pytest.mark.parametrize(
    ("error", "body"),
    [
        (ValidationError("Cannot render."), b'["Cannot render."]'),
        (ValidationError({"fields": ["nope"]}), b'{"fields":["nope"]}'),
    ],
    ids=["message", "mapping"],
)
def test_a_live_view_and_render_spec_output_answer_as_on_0_61_0(
    error: ValidationError, body: bytes
) -> None:
    post = Post.objects.create(title="t")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.get(pk=post.pk),
        output_serializer=_refusing(error),
    )

    class _View(SelectorRetrieveView):
        permission_classes: Any = []

    _View.spec = spec
    response = _View.as_view()(APIRequestFactory().get("/"))
    response.render()

    assert (response.status_code, response.content) == (400, body)
    with pytest.raises(ValidationError) as excinfo:
        render_spec_output(spec, post)
    assert type(excinfo.value) is ValidationError
    assert excinfo.value is error
