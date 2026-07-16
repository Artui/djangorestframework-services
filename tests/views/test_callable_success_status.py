"""Integration: ``ServiceSpec.success_status`` as a callable (upsert 200/201)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _UpsertResult:
    author: Author
    created: bool


def _upsert_author(*, data: _AuthorIn) -> _UpsertResult:
    author, created = Author.objects.get_or_create(name=data.name)
    return _UpsertResult(author=author, created=created)


def _refetch(*, result: _UpsertResult) -> Author:
    return Author.objects.get(pk=result.author.pk)


def _upsert_status(*, result: _UpsertResult) -> int:
    # 201 for a freshly created row, 200 for an existing one — keyed on the
    # *service's* return value, not the re-fetched output instance.
    return 201 if result.created else 200


def _archive_status(*, instance: Author) -> int:
    return 205


def _archive(*, instance: Author) -> None:
    # In-place mutation that returns nothing → empty body; the callable status
    # still applies uniformly (exercises the empty-body branch).
    instance.name = f"[archived] {instance.name}"
    instance.save(update_fields=["name"])


_UPSERT_SPEC: ServiceSpec = ServiceSpec(
    service=_upsert_author,
    input_serializer=_AuthorIn,
    success_status=_upsert_status,
    output_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_refetch,
        output_serializer=AuthorSerializer,
    ),
)


class _UpsertViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {"create": _UPSERT_SPEC}


class _ArchiveViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @service_action(
        ServiceSpec(service=_archive, success_status=_archive_status),
        detail=True,
        methods=["post"],
    )
    def archive(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Stubbed — replaced by service_action."""


factory = APIRequestFactory()


@pytest.mark.django_db
class TestCallableSuccessStatus:
    def test_create_returns_201_then_200_on_upsert(self) -> None:
        view = _UpsertViewSet.as_view({"post": "create"})

        first = view(factory.post("/", {"name": "Ada"}, format="json"))
        assert first.status_code == 201
        assert first.data["name"] == "Ada"

        # Same name again → get_or_create finds the row → 200, no duplicate.
        second = view(factory.post("/", {"name": "Ada"}, format="json"))
        assert second.status_code == 200
        assert Author.objects.filter(name="Ada").count() == 1

    def test_service_action_resolves_status_per_request_not_at_decoration(self) -> None:
        author = Author.objects.create(name="Grace")
        view = _ArchiveViewSet.as_view({"post": "archive"})
        # Empty-body response carrying the callable-resolved status (205).
        response = view(factory.post("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 205
        assert not response.data
        author.refresh_from_db()
        assert author.name == "[archived] Grace"
