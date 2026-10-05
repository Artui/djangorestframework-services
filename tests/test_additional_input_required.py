"""``AdditionalInputRequired`` — a service saying what it still needs.

The transport-neutral half of an interactive operation. Everything about *how*
a given protocol asks the question belongs to that protocol's transport; what
lives here is a service being able to say it without importing one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.test import override_settings
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import exception_handler
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (  # noqa: I001
    ActionSerializerResolver,
    AdditionalInputRequired,
    PolymorphicServiceSpec,
    SelectorKind,
    SelectorSpec,
    SelectorViewSet,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceError,
    ServiceSpec,
    ServiceUpdateView,
    ServiceValidationError,
    ServiceViewSet,
    call_service,
    dispatch_spec,
    service_action,
)
from rest_framework_services.views.mutation.map_service_error import map_service_error
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class _DeleteIn(serializers.Serializer):
    count = serializers.IntegerField()
    confirmed = serializers.BooleanField(required=False, default=False)


def _delete_rows(*, data: Any) -> dict[str, Any]:
    if data["count"] > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{data['count']} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    return {"deleted": data["count"]}


def _dispatch(**params: Any) -> Any:
    return dispatch_spec(
        ServiceSpec(service=_delete_rows, input_serializer=_DeleteIn, atomic=False),
        user=None,
        params=params,
    )


def test_it_carries_the_message_and_the_shape_of_what_is_missing() -> None:
    error = AdditionalInputRequired("Confirm first", schema={"confirmed": {"type": "boolean"}})
    assert str(error) == "Confirm first"
    assert error.schema == {"confirmed": {"type": "boolean"}}


def test_the_schema_is_optional() -> None:
    """A message alone is still useful — a transport that cannot render a form
    can show it."""
    assert AdditionalInputRequired("Confirm first").schema is None


def test_it_is_a_service_error() -> None:
    """Deliberate: a transport that has never heard of this still does
    something sensible — the operation could not be completed, and here is why.
    Transports that *can* ask catch it first and do better."""
    assert issubclass(AdditionalInputRequired, ServiceError)


def test_it_is_not_a_validation_error() -> None:
    """Different claim. A validation error says what you sent is wrong; this
    says the service got far enough to discover it needs something else —
    usually conditional on what it found, which is why it cannot be a required
    field on the serializer."""
    assert not issubclass(AdditionalInputRequired, ServiceValidationError)


def test_a_service_can_raise_it_mid_dispatch() -> None:
    with pytest.raises(AdditionalInputRequired) as caught:
        _dispatch(count=400)
    assert caught.value.schema == {"confirmed": {"type": "boolean"}}
    assert "400 rows match" in str(caught.value)


def test_the_answer_arrives_as_ordinary_input() -> None:
    """The reason the service's involvement ends at the raise: there is no
    callback to hold and no session to resume. Whatever the transport does to
    ask, the answer comes back through the parameters the service already
    declares."""
    result = _dispatch(count=400, confirmed=True)
    assert result.value == {"deleted": 400}


def test_a_service_that_needs_nothing_is_unaffected() -> None:
    result = _dispatch(count=1)
    assert result.value == {"deleted": 1}


def test_catching_service_error_still_catches_it() -> None:
    """The fallback a transport gets for free, and the reason ordering matters
    for one that wants to do better: a handler for ``ServiceError`` will
    swallow this unless it checks for this first."""
    with pytest.raises(ServiceError):
        _dispatch(count=400)


def test_the_mapped_http_error_carries_the_schema() -> None:
    """The half that was documented and not built.

    ``map_service_error``'s docstring said this error "stays a 422 and carries
    its own schema in the body", and ``AdditionalInputRequired``'s says "a
    transport that can ask renders it". The mapping dropped it: the response was
    the message alone, so an HTTP client was told something was missing and not
    what. drf-mcp renders the schema as an elicitation, which is why the gap
    showed only on the transport nobody tested it through.
    """
    mapped = map_service_error(
        AdditionalInputRequired("Confirm first", schema={"confirmed": {"type": "boolean"}})
    )

    assert mapped.status_code == 422
    assert mapped.detail["detail"] == "Confirm first"
    assert mapped.detail["schema"] == {"confirmed": {"type": "boolean"}}


def test_the_mapped_http_error_without_a_schema_keeps_the_plain_body() -> None:
    """``schema`` is optional, so the shape only grows when there is one to carry."""
    mapped = map_service_error(AdditionalInputRequired("Confirm first"))

    assert mapped.status_code == 422
    assert mapped.detail == "Confirm first"


def test_a_plain_service_error_is_unchanged() -> None:
    mapped = map_service_error(ServiceError("nope"))

    assert mapped.status_code == 422
    assert mapped.detail == "nope"


_PURGE_SCHEMA: dict[str, Any] = {
    "confirmed": {"type": "boolean", "default": False},
    "batches": {"type": "integer", "minimum": 1, "maximum": 3},
    "mode": {"enum": ["soft", None]},
}


def _purge() -> None:
    raise AdditionalInputRequired("Confirm the purge.", schema=_PURGE_SCHEMA)


_PURGE_SPEC = ServiceSpec(service=_purge, atomic=False)


class _PurgeView(ServiceCreateView):
    spec = _PURGE_SPEC


class _PurgeUpdateView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = _PURGE_SPEC


class _PurgeDeleteView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = _PURGE_SPEC


class _PurgeViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {"create": _PURGE_SPEC, "update": _PURGE_SPEC, "destroy": _PURGE_SPEC}

    @service_action(_PURGE_SPEC, detail=False, methods=["post"])
    def purge(self, request):  # type: ignore[no-untyped-def]
        """Replaced by service_action."""


class _PurgeSelectorViewSet(SelectorViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Author.objects.all(),
            output_serializer=AuthorSerializer,
        )
    }

    @service_action(_PURGE_SPEC, detail=False, methods=["post"])
    def purge(self, request):  # type: ignore[no-untyped-def]
        """Replaced by service_action."""


class _PurgeResolverViewSet(ActionSerializerResolver, GenericViewSet):
    """The lightest drfs base a hand-rolled viewset can take."""

    queryset = Author.objects.all()

    @service_action(_PURGE_SPEC, detail=False, methods=["post"])
    def purge(self, request):  # type: ignore[no-untyped-def]
        """Replaced by service_action."""


class _PurgePolymorphicViewSet(ServiceViewSet):
    """The discriminator raises, so the error is mapped while resolving the variant."""

    queryset = Author.objects.all()
    action_specs = {
        "create": PolymorphicServiceSpec(discriminator=_purge, specs={"only": _PURGE_SPEC})
    }


class _PurgePlainViewSet(GenericViewSet):
    """No drfs base at all: only the decorator is drfs'."""

    queryset = Author.objects.all()

    @service_action(_PURGE_SPEC, detail=False, methods=["post"])
    def purge(self, request):  # type: ignore[no-untyped-def]
        """Replaced by service_action."""


_factory = APIRequestFactory()

# Every class drfs ships that maps a ``ServiceError`` on the way to a response,
# reached the way a client reaches it. A detail route needs a row to load.
_ENTRY_POINTS: dict[str, tuple[Any, str, bool]] = {
    "ServiceCreateView": (_PurgeView.as_view(), "post", False),
    "ServiceUpdateView": (_PurgeUpdateView.as_view(), "put", True),
    "ServiceDeleteView": (_PurgeDeleteView.as_view(), "delete", True),
    "ServiceViewSet create": (_PurgeViewSet.as_view({"post": "create"}), "post", False),
    "ServiceViewSet update": (_PurgeViewSet.as_view({"put": "update"}), "put", True),
    "ServiceViewSet destroy": (_PurgeViewSet.as_view({"delete": "destroy"}), "delete", True),
    "service_action on ServiceViewSet": (_PurgeViewSet.as_view({"post": "purge"}), "post", False),
    "service_action on SelectorViewSet": (
        _PurgeSelectorViewSet.as_view({"post": "purge"}),
        "post",
        False,
    ),
    "service_action on ActionSerializerResolver": (
        _PurgeResolverViewSet.as_view({"post": "purge"}),
        "post",
        False,
    ),
    "PolymorphicServiceSpec discriminator": (
        _PurgePolymorphicViewSet.as_view({"post": "create"}),
        "post",
        False,
    ),
}


def _respond(view: Any, method: str, detail: bool) -> Any:
    kwargs = {"pk": Author.objects.create(name="a").pk} if detail else {}
    response = view(getattr(_factory, method)("/", {}, format="json"), **kwargs)
    response.render()
    return response


@pytest.mark.django_db
@pytest.mark.parametrize("entry", list(_ENTRY_POINTS))
def test_the_http_body_carries_the_schema_as_it_was_raised(entry: str) -> None:
    """Read off the rendered bytes, which is what a client parses.

    DRF coerces every leaf of an exception's detail to ``ErrorDetail``, a
    ``str``, so the schema used to arrive with ``"default": "False"`` and
    ``"maximum": "3"``: no longer JSON Schema, and a client rendering a form from
    it read a boolean default as a non-empty string. The detail keeps that shape
    for the configured handler's sake, and drfs' views put the schema back after
    it has built the response.
    """
    response = _respond(*_ENTRY_POINTS[entry])

    assert response.status_code == 422
    assert json.loads(response.content) == {"detail": "Confirm the purge.", "schema": _PURGE_SCHEMA}


class _PurgeResolverLastViewSet(GenericViewSet, ActionSerializerResolver):
    """``ActionSerializerResolver`` listed after ``GenericViewSet``, whose
    ``APIView.handle_exception`` then comes first in the MRO."""

    queryset = Author.objects.all()

    @service_action(_PURGE_SPEC, detail=False, methods=["post"])
    def purge(self, request):  # type: ignore[no-untyped-def]
        """Replaced by service_action."""


@pytest.mark.django_db
def test_action_serializer_resolver_restores_the_schema_only_before_generic_viewset() -> None:
    """The restore is a ``handle_exception`` override, so it runs only where
    the resolver precedes DRF's view in the bases; listed after, DRF's own
    method answers and never calls it."""
    body = json.loads(
        _respond(_PurgeResolverLastViewSet.as_view({"post": "purge"}), "post", False).content
    )

    assert body["schema"]["confirmed"] == {"type": "boolean", "default": "False"}


@pytest.mark.django_db
def test_a_viewset_with_no_drfs_base_serves_the_schema_as_drf_coerces_it() -> None:
    """The restore lives on drfs' bases, and ``@service_action`` is a decorator,
    so a viewset that takes none of them answers as a direct caller of
    ``map_service_error`` sees it: every leaf a string. The documented remedy is
    to compose any drfs base, ``ActionSerializerResolver`` being the lightest."""
    body = json.loads(
        _respond(_PurgePlainViewSet.as_view({"post": "purge"}), "post", False).content
    )

    assert body["schema"]["confirmed"] == {"type": "boolean", "default": "False"}
    assert body["schema"]["batches"]["maximum"] == "3"


def _flatten(detail: Any, attr: str = "") -> Iterator[dict[str, Any]]:
    """drf-standardized-errors' walk, restated so the test takes no dependency
    on it: every leaf of ``exc.detail`` is read for its ``.code``."""
    if isinstance(detail, dict):
        for key, value in detail.items():
            yield from _flatten(value, f"{attr}.{key}" if attr else key)
    elif isinstance(detail, list):
        for index, value in enumerate(detail):
            yield from _flatten(value, f"{attr}.{index}" if attr else str(index))
    else:
        yield {"code": detail.code, "detail": str(detail), "attr": attr}


def _standardized_errors(exc: Exception, context: dict[str, Any]) -> Response | None:
    if not isinstance(exc, APIException):
        return None
    return Response(
        {"type": "client_error", "errors": list(_flatten(exc.detail))}, status=exc.status_code
    )


def _bodiless(exc: Exception, context: dict[str, Any]) -> Response | None:
    """A handler that answers with the status alone, so ``response.data`` is ``None``."""
    if not isinstance(exc, APIException):
        return None
    return Response(status=exc.status_code)


def _plain_django(exc: Exception, context: dict[str, Any]) -> HttpResponse | None:
    """A handler answering with a Django response, which carries no ``.data``."""
    if not isinstance(exc, APIException):
        return None
    return JsonResponse({"error": str(exc.detail)}, status=exc.status_code)


_seen: list[Exception] = []


def _recording(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF's own handler, keeping hold of the exception it was given."""
    _seen.append(exc)
    return exception_handler(exc, context)


def _handled_by(name: str) -> Any:
    return override_settings(
        REST_FRAMEWORK={**settings.REST_FRAMEWORK, "EXCEPTION_HANDLER": f"{__name__}.{name}"}
    )


def test_a_handler_reading_every_leafs_code_answers_with_its_own_body() -> None:
    """The handler sees the detail it saw before the schema was carried as
    raised, and its body is left alone because it has no ``schema`` key of its
    own to restore.

    With native leaves in ``exc.detail``, this walk read ``.code`` off the
    ``str`` ``"boolean"`` and raised, so the ``422`` became a ``500``.
    """
    with _handled_by("_standardized_errors"):
        response = _PurgeView.as_view()(_factory.post("/", {}, format="json"))

    assert response.status_code == 422
    assert set(response.data) == {"type", "errors"}
    errors = response.data["errors"]
    assert errors[0] == {"code": "service_error", "detail": "Confirm the purge.", "attr": "detail"}
    assert {
        "code": "service_error",
        "detail": "False",
        "attr": "schema.confirmed.default",
    } in errors


def test_a_handler_that_answers_with_no_body_is_left_alone() -> None:
    with _handled_by("_bodiless"):
        response = _PurgeView.as_view()(_factory.post("/", {}, format="json"))

    assert response.status_code == 422
    assert response.data is None


def test_a_handler_answering_with_a_django_response_is_left_alone() -> None:
    """``EXCEPTION_HANDLER`` may return any ``HttpResponse``; DRF serves it as
    it is, so there is no ``.data`` to read a ``schema`` key from."""
    with _handled_by("_plain_django"):
        response = _PurgeView.as_view()(_factory.post("/", {}, format="json"))

    assert response.status_code == 422
    assert isinstance(response, JsonResponse)
    assert set(json.loads(response.content)) == {"error"}


def test_the_exception_keeps_drfs_shape_after_the_body_is_restored() -> None:
    """DRF's default handler answers with ``exc.detail`` itself, so restoring
    the schema in place would rewrite the exception too, and anything reading it
    after the response (a logger, an error tracker) would see a different shape
    from the one the handler saw."""
    _seen.clear()
    with _handled_by("_recording"):
        response = _PurgeView.as_view()(_factory.post("/", {}, format="json"))

    assert response.data["schema"] == _PURGE_SCHEMA
    assert _seen[0].detail["schema"]["confirmed"]["default"] == "False"  # ty: ignore[unresolved-attribute]


def _reject_schema_field() -> None:
    raise ServiceValidationError({"schema": ["Not a valid schema."]})


class _RejectView(ServiceCreateView):
    spec = ServiceSpec(service=_reject_schema_field, atomic=False)


def test_another_error_naming_a_schema_field_is_left_alone() -> None:
    """A ``schema`` key is restored only off the exception that carries one."""
    response = _RejectView.as_view()(_factory.post("/", {}, format="json"))

    assert response.status_code == 400
    assert response.data == {"schema": ["Not a valid schema."]}


def test_a_direct_caller_reads_the_raw_schema_off_the_exception() -> None:
    """``call_service(map_errors=True)`` has no view to restore it, so the detail
    is DRF's and the schema as raised is the exception's ``schema``."""
    with pytest.raises(APIException) as caught:
        call_service(_purge, request=_factory.post("/"), map_errors=True)

    assert caught.value.detail["schema"]["confirmed"]["default"] == "False"
    assert caught.value.schema == _PURGE_SCHEMA  # ty: ignore[unresolved-attribute]
