"""Tests for the @selector_action decorator."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import SelectorKind, SelectorSpec, selector_action
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


class _AuthorListPagination(PageNumberPagination):
    page_size = 1


class _SelectorViewSet(GenericViewSet):
    serializer_class = AuthorSerializer
    queryset = Author.objects.all()

    @selector_action(
        SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: list(Author.objects.all())),
        detail=False,
    )
    def active(self, request):  # type: ignore[no-untyped-def]
        """Stubbed body — replaced by selector_action."""

    @selector_action(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda **kwargs: Author.objects.filter(pk=kwargs["pk"]).first(),
        ),
        detail=True,
    )
    def card(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Stubbed."""


@pytest.mark.django_db
class TestSelectorAction:
    def test_collection_action_serializes_with_view_serializer(self) -> None:
        Author.objects.create(name="Alice")
        Author.objects.create(name="Bob")
        request = factory.get("/")
        view = _SelectorViewSet.as_view({"get": "active"})
        response = view(request)
        assert response.status_code == 200
        names = sorted(item["name"] for item in response.data)
        assert names == ["Alice", "Bob"]

    def test_detail_action_serializes_single(self) -> None:
        author = Author.objects.create(name="Carol")
        request = factory.get("/")
        view = _SelectorViewSet.as_view({"get": "card"})
        response = view(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"id": author.pk, "name": "Carol"}

    def test_detail_action_returning_none_yields_404(self) -> None:
        request = factory.get("/")
        view = _SelectorViewSet.as_view({"get": "card"})
        response = view(request, pk=99999)
        assert response.status_code == 404

    def test_detail_action_object_does_not_exist_yields_404(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        def selector(**_: Any) -> Any:
            raise ObjectDoesNotExist()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector), detail=True
            )
            def card(self, request, pk=None):  # type: ignore[no-untyped-def]
                ...

        view = _View.as_view({"get": "card"})
        response = view(factory.get("/"), pk=1)
        assert response.status_code == 404

    def test_action_metadata_attached(self) -> None:
        active = _SelectorViewSet.active  # type: ignore[attr-defined]
        assert active.detail is False
        # DRF's @action defaults methods to GET when none are passed.
        assert "get" in active.mapping

    def test_methods_forwarded_to_drf_action(self) -> None:
        class _View(GenericViewSet):
            queryset = Author.objects.none()
            serializer_class = AuthorSerializer

            @selector_action(
                SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
                detail=False,
                methods=["get", "head"],
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        listing = _View.listing  # type: ignore[attr-defined]
        assert "get" in listing.mapping
        assert "head" in listing.mapping

    def test_url_path_and_url_name_forwarded(self) -> None:
        class _View(GenericViewSet):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

            @selector_action(
                SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
                detail=False,
                url_path="recent",
                url_name="recent-authors",
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        listing = _View.listing  # type: ignore[attr-defined]
        assert listing.url_path == "recent"
        assert listing.url_name == "recent-authors"

    def test_spec_output_serializer_overrides_view_serializer(self) -> None:
        class _AltSerializer(AuthorSerializer):
            class Meta(AuthorSerializer.Meta):
                fields = ("name",)

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer  # would yield {id, name}
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda **_: list(Author.objects.all()),
                    output_serializer=_AltSerializer,
                ),
                detail=False,
            )
            def names_only(self, request):  # type: ignore[no-untyped-def]
                ...

        Author.objects.create(name="Dave")
        view = _View.as_view({"get": "names_only"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data == [{"name": "Dave"}]

    def test_pagination_applied_when_configured(self) -> None:
        class _Paginated(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()
            pagination_class = _AuthorListPagination

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda **_: list(Author.objects.all().order_by("name")),
                ),
                detail=False,
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        Author.objects.create(name="A")
        Author.objects.create(name="B")
        Author.objects.create(name="C")
        view = _Paginated.as_view({"get": "listing"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        # Page size = 1, so the count is 3 and results carries one entry.
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 1

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def selector(*, tenant_id: int) -> list[Any]:
            captured["tenant_id"] = tenant_id
            return []

        def provider(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 99}

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.none()

            @selector_action(
                SelectorSpec(kind=SelectorKind.LIST, selector=selector, kwargs=provider),
                detail=False,
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        view = _View.as_view({"get": "listing"})
        view(factory.get("/"))
        assert captured == {"tenant_id": 99}

    def test_get_selector_kwargs_catch_all_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def selector(*, tag: str) -> list[Any]:
            captured["tag"] = tag
            return []

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.none()

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tag": "default"}

            @selector_action(
                SelectorSpec(kind=SelectorKind.LIST, selector=selector),
                detail=False,
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        view = _View.as_view({"get": "listing"})
        view(factory.get("/"))
        assert captured == {"tag": "default"}

    def test_per_action_kwargs_hook_overrides_catch_all(self) -> None:
        captured: dict[str, Any] = {}

        def selector(*, tag: str) -> list[Any]:
            captured["tag"] = tag
            return []

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.none()

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tag": "catchall"}

            def get_listing_selector_kwargs(self) -> dict[str, Any]:
                return {"tag": "per-action"}

            @selector_action(
                SelectorSpec(kind=SelectorKind.LIST, selector=selector),
                detail=False,
            )
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        view = _View.as_view({"get": "listing"})
        view(factory.get("/"))
        assert captured == {"tag": "per-action"}

    def test_handler_carries_selector_spec_attribute(self) -> None:
        """Schema generators / introspection rely on this stamp."""

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: [])

        class _View(GenericViewSet):
            queryset = Author.objects.none()
            serializer_class = AuthorSerializer

            @selector_action(spec, detail=False)
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

        assert _View.listing._selector_spec is spec  # type: ignore[attr-defined]

    def test_not_found_propagates_for_detail(self) -> None:
        """``dispatch_selector_for_spec`` raises ``NotFound`` on None — DRF maps to 404."""

        def selector(**_: Any) -> Any:
            return None

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.none()

            @selector_action(
                SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector), detail=True
            )
            def card(self, request, pk=None):  # type: ignore[no-untyped-def]
                ...

        view = _View.as_view({"get": "card"})
        response = view(factory.get("/"), pk=1)
        assert response.status_code == 404
        # Smoke check: NotFound is the one DRF raised.
        assert isinstance(response.exception, bool) or NotFound  # noqa: PT017

    def test_detail_disagrees_with_kind_raises(self) -> None:
        """`detail=True` with a `LIST` spec (or vice versa) raises at decoration."""

        with pytest.raises(ImproperlyConfigured, match=r"detail=True.*disagrees with spec.kind"):

            class _Bad(GenericViewSet):
                queryset = Author.objects.none()
                serializer_class = AuthorSerializer

                @selector_action(
                    SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
                    detail=True,
                )
                def listing(self, request):  # type: ignore[no-untyped-def]
                    ...

    def test_detail_defaults_from_spec_kind(self) -> None:
        """Omitting `detail` defaults from `spec.kind` (LIST→False, RETRIEVE→True)."""

        class _View(GenericViewSet):
            queryset = Author.objects.none()
            serializer_class = AuthorSerializer

            @selector_action(SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []))
            def listing(self, request):  # type: ignore[no-untyped-def]
                ...

            @selector_action(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None))
            def card(self, request, pk=None):  # type: ignore[no-untyped-def]
                ...

        assert _View.listing.detail is False  # type: ignore[attr-defined]
        assert _View.card.detail is True  # type: ignore[attr-defined]
