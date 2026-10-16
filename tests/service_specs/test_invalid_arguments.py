"""Tests for the dual-base ``InvalidArguments``: the kernel's refusal that DRF answers 400.

A response body cannot tell a working class from a broken one. When the
kernel's ``__init__`` alone runs, ``detail`` is a tree of plain strings, and
DRF's handler still answers 400 with a byte-identical body, while
``get_codes()`` raises. So these assert the codes and the ``ErrorDetail`` type,
and the status assertions are paired with the kernel's own class, which DRF's
handler does not recognise and Django answers 500.
"""

from __future__ import annotations

import pytest
from django_service_specs.parameters.coded_message import CodedMessage
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.services.service_error import ServiceError as KernelServiceError
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import ErrorDetail, ValidationError

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from tests.service_specs.served_status import served_status


def _tree() -> dict[object, object]:
    """A field with a coded message, and a row of a collection with a plain one."""
    return {
        "title": [CodedMessage("This field is required.", "required")],
        "books": {1: {"name": ["Not a name."]}},
    }


class TestItIsBothClasses:
    def test_it_is_the_kernels_refusal_and_drfs(self) -> None:
        assert issubclass(InvalidArguments, KernelInvalidArguments)
        assert issubclass(InvalidArguments, DispatchError)
        assert issubclass(InvalidArguments, ValidationError)

    @pytest.mark.parametrize("service_error", [KernelServiceError, ServiceError])
    def test_it_is_never_a_service_error(self, service_error: type[Exception]) -> None:
        # A service refusal is something an agent routes around; a refusal of
        # the arguments is not, and an MCP server tells the two apart by type.
        assert not issubclass(InvalidArguments, service_error)


class TestDrfsInitRan:
    def test_every_message_is_an_error_detail(self) -> None:
        exc = InvalidArguments(_tree())
        assert isinstance(exc.detail["title"][0], ErrorDetail)
        assert isinstance(exc.detail["books"][1]["name"][0], ErrorDetail)

    def test_get_codes_keeps_the_kernels_code_and_defaults_the_rest(self) -> None:
        exc = InvalidArguments(_tree())
        assert exc.get_codes() == {
            "title": ["required"],
            "books": {1: {"name": ["invalid"]}},
        }

    def test_a_code_given_is_the_default_for_messages_carrying_none(self) -> None:
        exc = InvalidArguments(_tree(), code="refused")
        assert exc.get_codes() == {
            "title": ["required"],
            "books": {1: {"name": ["refused"]}},
        }

    def test_the_kernels_message_is_kept(self) -> None:
        assert InvalidArguments(_tree()).message == KernelInvalidArguments.default_message

    def test_it_reads_as_drfs_validation_error_does(self) -> None:
        # A ladder that turns the refusal into text, as an agent retry does,
        # reads what it read when the class raised was DRF's alone.
        assert str(InvalidArguments(_tree())) == str(ValidationError(_tree()))


class TestDrfAnswersIt:
    def test_drf_answers_it_400(self) -> None:
        assert served_status(InvalidArguments(_tree())) == 400

    def test_the_kernels_class_alone_is_a_500(self) -> None:
        # The DRF base is what earns the 400: without it, DRF's handler returns
        # nothing and the view's exception reaches Django unanswered.
        assert served_status(KernelInvalidArguments(_tree())) == 500
