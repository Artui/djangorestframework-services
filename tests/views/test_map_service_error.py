"""Tests for views/mutation/map_service_error.py."""

from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status

from rest_framework_services.exceptions import (
    AdditionalInputRequired,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceValidationError,
)
from rest_framework_services.views.mutation.map_service_error import (
    _ConflictAPIException,
    _ServiceAPIException,
    map_service_error,
)


class TestMapServiceError:
    def test_validation_error_maps_to_drf_validation(self) -> None:
        exc = map_service_error(ServiceValidationError({"name": ["required"]}))
        assert isinstance(exc, drf_exceptions.ValidationError)
        assert exc.detail == {"name": ["required"]}

    def test_generic_service_error_maps_to_422(self) -> None:
        exc = map_service_error(ServiceError("nope"))
        assert isinstance(exc, _ServiceAPIException)
        assert exc.status_code == drf_status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_not_found_maps_to_404(self) -> None:
        exc = map_service_error(ServiceNotFound("No event 7."))
        assert isinstance(exc, drf_exceptions.NotFound)
        assert exc.status_code == drf_status.HTTP_404_NOT_FOUND
        assert str(exc.detail) == "No event 7."

    def test_conflict_maps_to_409(self) -> None:
        exc = map_service_error(ServiceConflict("That slot is taken."))
        assert isinstance(exc, _ConflictAPIException)
        assert exc.status_code == drf_status.HTTP_409_CONFLICT
        assert str(exc.detail) == "That slot is taken."

    def test_a_subclass_of_a_member_keeps_that_members_status(self) -> None:
        """The whole point of the members: a project names its own rule.

        ``class SlotTaken(ServiceConflict)`` is how this is meant to be used, and
        the mapping has to follow the class rather than the exact type.
        """

        class SlotTaken(ServiceConflict):
            default_message = "That slot is taken."

        assert map_service_error(SlotTaken()).status_code == drf_status.HTTP_409_CONFLICT

    def test_additional_input_required_stays_a_422(self) -> None:
        """ "I need one more value" is unprocessable-as-asked, not absent or colliding."""
        exc = map_service_error(AdditionalInputRequired("Confirm to proceed."))
        assert isinstance(exc, _ServiceAPIException)
        assert exc.status_code == drf_status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_the_generic_branch_is_last(self) -> None:
        """Every member is a ``ServiceError``, so the order *is* the mapping.

        A generic branch reached first would answer 422 to all of them — the same
        trap a transport's own handler has, and the reason each member's docstring
        says to match before the generic one.
        """
        statuses = {
            type(error).__name__: map_service_error(error).status_code
            for error in (ServiceNotFound(), ServiceConflict(), ServiceError())
        }
        assert statuses == {
            "ServiceNotFound": drf_status.HTTP_404_NOT_FOUND,
            "ServiceConflict": drf_status.HTTP_409_CONFLICT,
            "ServiceError": drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
        }


class TestServiceAPIException:
    def test_default_detail(self) -> None:
        exc = _ServiceAPIException()
        assert exc.detail == "Service error."
        assert exc.status_code == 422

    def test_custom_detail(self) -> None:
        exc = _ServiceAPIException("custom")
        assert str(exc.detail) == "custom"


class TestConflictAPIException:
    def test_default_detail(self) -> None:
        exc = _ConflictAPIException()
        assert exc.detail == "Conflict."
        assert exc.status_code == 409
        assert exc.default_code == "conflict"

    def test_custom_detail(self) -> None:
        assert str(_ConflictAPIException("custom").detail) == "custom"


class TestMemberDefaults:
    def test_each_member_has_a_message_of_its_own(self) -> None:
        assert str(ServiceNotFound()) == "Not found."
        assert str(ServiceConflict()) == "Conflict."

    def test_each_member_is_a_service_error(self) -> None:
        """So a transport that has never heard of them still handles them."""
        assert isinstance(ServiceNotFound(), ServiceError)
        assert isinstance(ServiceConflict(), ServiceError)
