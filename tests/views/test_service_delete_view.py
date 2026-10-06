"""Tests for ServiceDeleteView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorSpec, ServiceDeleteView, ServiceSpec
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _delete_author(*, instance: Author) -> None:
    instance.delete()


class _DeleteAuthorView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_author)


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceDeleteView:
    def test_returns_204_by_default(self) -> None:
        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _DeleteAuthorView.as_view()(request, pk=author.pk)
        assert response.status_code == 204
        assert not Author.objects.exists()

    def test_404_when_missing(self) -> None:
        request = factory.delete("/")
        response = _DeleteAuthorView.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_with_input_serializer(self) -> None:
        @dataclass
        class _Reason:
            reason: str

        captured: dict[str, Any] = {}

        def fn(*, instance: Author, data: _Reason) -> None:
            captured["reason"] = data.reason
            instance.delete()

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=fn, input_serializer=_Reason)

        author = Author.objects.create(name="x")
        request = factory.delete("/", {"reason": "spam"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 204
        assert captured["reason"] == "spam"

    def test_with_output_serializer_returns_body(self) -> None:
        def fn(*, instance: Author) -> Author:
            instance.delete()
            return instance

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                success_status=200,
            )

        author = Author.objects.create(name="bye")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "bye"

    def test_missing_spec_raises(self) -> None:
        class _Empty(ServiceDeleteView):
            queryset = Author.objects.all()

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request, pk=author.pk)

    def test_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, instance: Author) -> None:
            raise ServiceError("nope")

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=boom, atomic=False)

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 422

    def test_output_selector_used_when_set(self) -> None:
        def fn(*, instance: Author) -> Author:
            return instance

        def selector(*, result: Author) -> dict[str, Any]:
            return {"name_upper": result.name.upper()}

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
                success_status=200,
                atomic=False,
            )

        author = Author.objects.create(name="bye")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"name_upper": "BYE"}

    def test_input_data_merged_into_serializer_input(self) -> None:
        @dataclass
        class _ReasonWithParent:
            reason: str
            parent_id: int

        captured: dict[str, Any] = {}

        def fn(*, instance: Author, data: _ReasonWithParent) -> None:
            captured["data"] = data
            instance.delete()

        def spec_input(view: Any, request: Any) -> dict[str, Any]:
            return {"parent_id": int(view.kwargs.get("parent_id", 0))}

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn,
                input_serializer=_ReasonWithParent,
                input_data=spec_input,
            )

        author = Author.objects.create(name="x")
        request = factory.delete("/", {"reason": "spam"}, format="json")
        response = _View.as_view()(request, pk=author.pk, parent_id=7)
        assert response.status_code == 204
        assert captured["data"].reason == "spam"
        assert captured["data"].parent_id == 7

    def test_returning_value_with_success_status_override(self) -> None:
        def fn(*, instance: Author) -> dict[str, Any]:
            return {"deleted": instance.pk}

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=fn, success_status=200, atomic=False)

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"deleted": author.pk}

    @pytest.mark.parametrize(("success_status", "expected"), [(None, 204), (202, 202)])
    def test_an_output_serializer_never_renders_the_deleted_row(
        self, success_status: int | None, expected: int
    ) -> None:
        """A destroy whose service returns ``None`` sends an empty body even where
        the spec declares an ``output_serializer``, at the explicitly-set
        ``success_status``, else ``204``. Rendering the serializer over ``None``
        sent a row of blank fields under the ``204``."""

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_delete_author,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                success_status=success_status,
                atomic=False,
            )

        author = Author.objects.create(name="x")
        response = _View.as_view()(factory.delete("/"), pk=author.pk)
        assert response.status_code == expected
        assert response.data is None
        assert response.render().content == b""
        assert not Author.objects.filter(pk=author.pk).exists()

    def test_none_returning_service_honors_custom_success_status_with_empty_body(
        self,
    ) -> None:
        """A delete whose service returns ``None`` must honor a custom
        ``success_status`` and render an *empty* body — not the stale
        post-delete instance (which is unserializable)."""

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=_delete_author, success_status=202, atomic=False)

        author = Author.objects.create(name="x")
        response = _View.as_view()(factory.delete("/"), pk=author.pk)
        assert response.status_code == 202
        assert response.data is None
        # The response must actually render (a raw model instance would not).
        assert response.render().content == b""
        assert not Author.objects.filter(pk=author.pk).exists()
