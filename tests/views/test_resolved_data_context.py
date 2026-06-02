"""Resolved-data extras passed to output serializer-context providers.

Covers the widened context-provider contract: a provider may declare a
keyword for the data about to be serialized — ``result`` (mutation output),
``instance`` (retrieve), or ``page`` (list) — and receive it only when
declared, so it can run a *single* batched query against the exact objects
and propagate the outcome through ``context``. Legacy ``(view, request)``
providers must be unaffected.

The mechanism lives in
:func:`rest_framework_services.views.utils._invoke_with_extras`; these tests
exercise it end-to-end through every entry point plus a few unit-level
assertions on the helper itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    SelectorViewSet,
    ServiceCreateView,
    ServiceSpec,
    ServiceViewSet,
    selector_action,
)
from rest_framework_services.views.utils import _invoke_with_extras
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


def _capturing_serializer_factory() -> type[serializers.ModelSerializer]:
    """Fresh subclass per test so the ``captured`` class var is isolated."""

    class _Capturing(serializers.ModelSerializer):
        captured: dict[str, Any] = {}

        class Meta:
            model = Author
            fields = ("id", "name")

        def to_representation(self, instance: Any) -> Any:
            type(self).captured = dict(self.context)
            return super().to_representation(instance)

    return _Capturing


class _SmallPage(PageNumberPagination):
    page_size = 2


# --- unit: _invoke_with_extras -------------------------------------------


class TestInvokeWithExtras:
    def test_legacy_two_arg_provider_gets_exactly_view_and_request(self) -> None:
        seen: dict[str, Any] = {}

        def provider(view: Any, request: Any) -> dict[str, Any]:
            seen["args"] = (view, request)
            return {}

        _invoke_with_extras(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
        # The undeclared extras must not leak in — called with exactly two.
        assert seen["args"] == ("VIEW", "REQ")

    def test_declared_extra_is_passed_by_keyword(self) -> None:
        seen: dict[str, Any] = {}

        def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
            seen["result"] = result
            return {}

        _invoke_with_extras(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
        assert seen["result"] == "R"

    def test_var_keyword_provider_receives_all_extras(self) -> None:
        seen: dict[str, Any] = {}

        def provider(view: Any, request: Any, **extra: Any) -> dict[str, Any]:
            seen.update(extra)
            return {}

        _invoke_with_extras(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
        assert seen == {"result": "R", "page": "P"}

    def test_bound_view_hook_no_leading_args(self) -> None:
        seen: dict[str, Any] = {}

        def hook(*, page: Any) -> dict[str, Any]:
            seen["page"] = page
            return {}

        _invoke_with_extras(hook, extras={"page": "P"})
        assert seen["page"] == "P"


# --- mutation: the ``result`` extra --------------------------------------


@pytest.mark.django_db
class TestMutationResultExtra:
    def test_create_provider_receives_service_result(self) -> None:
        capturing = _capturing_serializer_factory()
        seen: dict[str, Any] = {}

        def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
            seen["result"] = result
            return {"result_pk": result.pk}

        author = Author.objects.create(name="seed")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: author),
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    output_serializer=capturing,
                    output_serializer_context=provider,
                ),
            )

        _View.as_view()(factory.post("/", {}, format="json"))
        assert seen["result"] == author
        assert capturing.captured["result_pk"] == author.pk

    def test_result_is_post_selector_instance(self) -> None:
        """When an output selector re-fetches, the provider sees the
        re-fetched instance, not the service's raw return."""
        capturing = _capturing_serializer_factory()
        seen: dict[str, Any] = {}

        raw = Author.objects.create(name="raw")
        refetched_pk = raw.pk

        def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
            seen["same_object_as_raw"] = result is raw
            seen["pk"] = result.pk
            return {}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: raw),
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda result: Author.objects.filter(pk=result.pk),
                    output_serializer=capturing,
                    output_serializer_context=provider,
                ),
            )

        _View.as_view()(factory.post("/", {}, format="json"))
        # Re-fetched via the selector → a distinct instance with the same pk.
        assert seen["same_object_as_raw"] is False
        assert seen["pk"] == refetched_pk

    def test_legacy_two_arg_output_provider_still_works(self) -> None:
        capturing = _capturing_serializer_factory()
        author = Author.objects.create(name="seed")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: author),
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req: {"legacy": True},
                ),
            )

        _View.as_view()(factory.post("/", {}, format="json"))
        assert capturing.captured["legacy"] is True

    def test_per_action_view_hook_receives_result_on_viewset(self) -> None:
        capturing = _capturing_serializer_factory()

        def _create() -> Author:
            return Author.objects.create(name="made")

        class _ViewSet(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "create": ServiceSpec(
                    service=staticmethod(_create),
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        output_serializer=capturing,
                    ),
                ),
            }

            def get_create_output_serializer_context(self, *, result: Any) -> dict[str, Any]:
                return {"hook_pk": result.pk}

        _ViewSet.as_view({"post": "create"})(factory.post("/", {}, format="json"))
        assert capturing.captured["hook_pk"] is not None


# --- retrieve: the ``instance`` extra ------------------------------------


@pytest.mark.django_db
class TestRetrieveInstanceExtra:
    def test_standalone_retrieve_view(self) -> None:
        author = Author.objects.create(name="x")
        capturing = _capturing_serializer_factory()

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda pk: Author.objects.filter(pk=pk).first(),
                output_serializer=capturing,
                output_serializer_context=lambda view, req, *, instance: {
                    "instance_pk": instance.pk
                },
            )

        _View.as_view()(factory.get("/"), pk=author.pk)
        assert capturing.captured["instance_pk"] == author.pk

    def test_retrieve_viewset_mixin(self) -> None:
        author = Author.objects.create(name="x")
        capturing = _capturing_serializer_factory()

        class _ViewSet(SelectorViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda pk: Author.objects.filter(pk=pk).first(),
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req, *, instance: {
                        "instance_pk": instance.pk
                    },
                ),
            }

        _ViewSet.as_view({"get": "retrieve"})(factory.get("/"), pk=author.pk)
        assert capturing.captured["instance_pk"] == author.pk


# --- list: the ``page`` extra --------------------------------------------


@pytest.mark.django_db
class TestListPageExtra:
    def test_standalone_list_view_paginated_sees_page(self) -> None:
        for i in range(5):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        class _View(SelectorListView):
            pagination_class = _SmallPage
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: Author.objects.all().order_by("id"),
                output_serializer=capturing,
                output_serializer_context=lambda view, req, *, page: {"page_size": len(page)},
            )

        _View.as_view()(factory.get("/"))
        # Only the page (size 2), not the full 5-row result.
        assert capturing.captured["page_size"] == 2

    def test_standalone_list_view_unpaginated_sees_full_queryset(self) -> None:
        for i in range(3):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        class _View(SelectorListView):
            pagination_class = None
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: Author.objects.all().order_by("id"),
                output_serializer=capturing,
                output_serializer_context=lambda view, req, *, page: {"n": len(list(page))},
            )

        _View.as_view()(factory.get("/"))
        assert capturing.captured["n"] == 3

    def test_list_viewset_mixin_sees_page(self) -> None:
        for i in range(5):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        class _ViewSet(SelectorViewSet):
            pagination_class = _SmallPage
            queryset = Author.objects.all()
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: Author.objects.all().order_by("id"),
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req, *, page: {"page_size": len(page)},
                ),
            }

        _ViewSet.as_view({"get": "list"})(factory.get("/"))
        assert capturing.captured["page_size"] == 2

    def test_batched_query_over_page_is_single(self, django_assert_num_queries: Any) -> None:
        """The provider can run exactly one extra query against the page."""
        for i in range(5):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        def provider(view: Any, request: Any, *, page: Any) -> dict[str, Any]:
            ids = [a.pk for a in page]
            # One batched query keyed on the page's ids.
            names = dict(Author.objects.filter(pk__in=ids).values_list("pk", "name"))
            return {"names": names}

        class _View(SelectorListView):
            pagination_class = _SmallPage
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: Author.objects.all().order_by("id"),
                output_serializer=capturing,
                output_serializer_context=provider,
            )

        # count(1) + page(1) + the provider's single batched query(1) = 3.
        with django_assert_num_queries(3):
            _View.as_view()(factory.get("/"))
        assert len(capturing.captured["names"]) == 2


# --- @selector_action ----------------------------------------------------


@pytest.mark.django_db
class TestSelectorActionExtras:
    def test_detail_action_receives_instance(self) -> None:
        author = Author.objects.create(name="x")
        capturing = _capturing_serializer_factory()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda pk: Author.objects.filter(pk=pk).first(),
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req, *, instance: {
                        "instance_pk": instance.pk
                    },
                ),
            )
            def detail_one(self, request, pk=None):  # type: ignore[no-untyped-def]
                """Stubbed."""

        _View.as_view({"get": "detail_one"})(factory.get("/"), pk=author.pk)
        assert capturing.captured["instance_pk"] == author.pk

    def test_collection_action_paginated_receives_page(self) -> None:
        for i in range(5):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()
            pagination_class = _SmallPage

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: Author.objects.all().order_by("id"),
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req, *, page: {"page_size": len(page)},
                ),
            )
            def many(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        _View.as_view({"get": "many"})(factory.get("/"))
        assert capturing.captured["page_size"] == 2

    def test_collection_action_unpaginated_receives_result(self) -> None:
        for i in range(3):
            Author.objects.create(name=f"a{i}")
        capturing = _capturing_serializer_factory()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()
            pagination_class = None

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: list(Author.objects.all().order_by("id")),
                    output_serializer=capturing,
                    output_serializer_context=lambda view, req, *, page: {"n": len(page)},
                ),
            )
            def many(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        _View.as_view({"get": "many"})(factory.get("/"))
        assert capturing.captured["n"] == 3


# --- mutation single-query guarantee -------------------------------------


@pytest.mark.django_db
class TestMutationBatchedQuery:
    def test_provider_single_query_against_result(self, django_assert_num_queries: Any) -> None:
        capturing = _capturing_serializer_factory()

        @dataclass
        class _In:
            name: str

        def _create(*, data: _In) -> Author:
            return Author.objects.create(name=data.name)

        def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
            # A single aggregate keyed on the created instance.
            return {"total": Author.objects.filter(pk=result.pk).count()}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create,
                input_serializer=_In,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    output_serializer=capturing,
                    output_serializer_context=provider,
                ),
            )

        request = factory.post("/", {"name": "Ada"}, format="json")
        # insert(1) + provider count(1) = 2.
        with django_assert_num_queries(2):
            _View.as_view()(request)
        assert capturing.captured["total"] == 1
