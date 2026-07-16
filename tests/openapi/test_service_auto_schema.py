"""End-to-end schema generation with drf-spectacular + ``ServiceAutoSchema``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import django_filters
import pytest
from django.urls import path
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.generators import SchemaGenerator
from rest_framework.routers import DefaultRouter
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    service_action,
)
from rest_framework_services.openapi import enable_openapi
from rest_framework_services.openapi.service_auto_schema import (
    ServiceAutoSchema,
    _filter_set_parameters,
    _SpecFilterBackend,
)
from tests.testapp.models import Author, Post
from tests.testapp.serializers import AuthorSerializer, PostSerializer


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _AuthorOut:
    id: int
    name: str


def _create(*, data: _AuthorIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.name)


def _update(*, instance: Any, data: _AuthorIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.name)


def _approve(*, instance: Any) -> dict[str, Any]:
    return {"approved": True}


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


class _CreateView(ServiceCreateView):
    spec = ServiceSpec(
        service=_create,
        input_serializer=_AuthorIn,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _UpdateView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_update,
        input_serializer=_AuthorIn,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


@dataclass
class _DeleteReason:
    reason: str


def _delete_with_reason(*, instance: Any, data: _DeleteReason) -> None: ...


class _DeleteView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_with_reason, input_serializer=_DeleteReason)


def _delete_plain(*, instance: Any) -> None: ...


class _DeletePlainView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_plain)


class _ForcedErrorDeleteView(ServiceDeleteView):
    # No input_serializer, but ``document_service_error=True`` forces the 422
    # back on (a no-input service that *does* raise ServiceError).
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_plain, document_service_error=True)


class _NoErrorCreateView(ServiceCreateView):
    # Input-bearing, but ``document_service_error=False`` drops the 422.
    spec = ServiceSpec(
        service=_create,
        input_serializer=_AuthorIn,
        document_service_error=False,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


def _dynamic_status(*, result: _AuthorOut) -> int:
    return 201 if result.id else 200


class _CallableStatusCreateView(ServiceCreateView):
    # A callable ``success_status`` can't be resolved statically, so the schema
    # documents the create default (201).
    spec = ServiceSpec(
        service=_create,
        input_serializer=_AuthorIn,
        success_status=_dynamic_status,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=_create,
            input_serializer=_AuthorIn,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
            ),
        ),
        "list": SelectorSpec(
            kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
        ),
    }


class _ApproveViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @service_action(
        ServiceSpec(
            service=_approve,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
            ),
        ),
        detail=True,
        methods=["post"],
    )
    def approve(self, request, pk=None):  # type: ignore[no-untyped-def]
        ...


class _ForcedFullUpdateView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_update,
        input_serializer=_AuthorIn,
        partial=False,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


# --- filter_set → OpenAPI parameters (parity fixtures) -----------------------


class _PostFilter(django_filters.FilterSet):
    """Exercises field, choice, multi-choice, and ordering filter shapes."""

    title = django_filters.CharFilter(lookup_expr="icontains")
    published = django_filters.BooleanFilter()
    views = django_filters.NumberFilter()
    kind = django_filters.ChoiceFilter(field_name="body", choices=[("a", "A"), ("b", "B")])
    labels = django_filters.MultipleChoiceFilter(
        field_name="title", choices=[("x", "X"), ("y", "Y")]
    )
    order = django_filters.OrderingFilter(fields=(("title", "title"), ("views", "views")))

    class Meta:
        model = Post
        fields: list[str] = []


def _list_posts() -> Any:
    return Post.objects.all().order_by("id")


class _ViewLevelFilterListView(SelectorListView):
    """The *before* config: FilterSet on the view via ``DjangoFilterBackend``."""

    queryset = Post.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = _PostFilter
    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_list_posts, output_serializer=PostSerializer
    )


class _SpecFilterListView(SelectorListView):
    """The *after* config: FilterSet on the spec, no backend."""

    queryset = Post.objects.all()
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_list_posts,
        output_serializer=PostSerializer,
        filter_set=_PostFilter,
    )


class _ViewLevelFilterRetrieveView(SelectorRetrieveView):
    queryset = Post.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = _PostFilter
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_list_posts, output_serializer=PostSerializer
    )


class _SpecFilterRetrieveView(SelectorRetrieveView):
    queryset = Post.objects.all()
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_list_posts,
        output_serializer=PostSerializer,
        filter_set=_PostFilter,
    )


_router = DefaultRouter()
_router.register("authors", _AuthorViewSet, basename="authors")
_router.register("approvals", _ApproveViewSet, basename="approvals")

urlpatterns = [
    path("create/", _CreateView.as_view()),
    path("update/<int:pk>/", _UpdateView.as_view()),
    path("forced-full/<int:pk>/", _ForcedFullUpdateView.as_view()),
    path("delete/<int:pk>/", _DeleteView.as_view()),
    path("plain-delete/<int:pk>/", _DeletePlainView.as_view()),
    path("force-error-delete/<int:pk>/", _ForcedErrorDeleteView.as_view()),
    path("no-error-create/", _NoErrorCreateView.as_view()),
    path("callable-status-create/", _CallableStatusCreateView.as_view()),
    path("posts-viewlevel/", _ViewLevelFilterListView.as_view()),
    path("posts-spec/", _SpecFilterListView.as_view()),
    path("post-viewlevel/<int:pk>/", _ViewLevelFilterRetrieveView.as_view()),
    path("post-spec/<int:pk>/", _SpecFilterRetrieveView.as_view()),
    *_router.urls,
]


@pytest.fixture(scope="module", autouse=True)
def _enable_openapi() -> None:
    enable_openapi()


def _generate() -> dict[str, Any]:
    return SchemaGenerator(patterns=urlpatterns).get_schema(request=None, public=True)


@pytest.mark.django_db
class TestStandaloneViewSchema:
    def test_create_view_emits_201_response(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        assert "201" in op["responses"]
        assert "422" in op["responses"]

    def test_create_view_request_body_uses_input_serializer(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        # The schema is a $ref to a component derived from ``_AuthorIn``;
        # follow it to verify the component has typed properties (not a bare
        # ``object``).
        ref = body["$ref"]
        assert ref.startswith("#/components/schemas/")
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert set(component.get("properties", {}).keys()) == {"name"}

    def test_partial_update_request_serializer_marked_partial(self) -> None:
        schema = _generate()
        op = schema["paths"]["/update/{id}/"]["patch"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        # spectacular emits a ``Patched*`` component for partial requests.
        assert "Patched" in ref or component.get("required") in (None, [])

    def test_spec_partial_false_overrides_patch_partiality(self) -> None:
        schema = _generate()
        op = schema["paths"]["/forced-full/{id}/"]["patch"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        # ``partial=False`` forces full-update semantics, so no ``Patched*``
        # component and the required list survives under PATCH.
        assert "Patched" not in ref
        assert component.get("required") == ["name"]


@pytest.mark.django_db
class TestDeleteViewSchema:
    def test_delete_view_emits_request_body_when_input_serializer_set(self) -> None:
        schema = _generate()
        op = schema["paths"]["/delete/{id}/"]["delete"]
        assert "requestBody" in op
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert set(component.get("properties", {}).keys()) == {"reason"}

    def test_delete_view_omits_request_body_when_no_input_serializer(self) -> None:
        schema = _generate()
        op = schema["paths"]["/plain-delete/{id}/"]["delete"]
        assert "requestBody" not in op


@pytest.mark.django_db
class TestViewsetSchema:
    def test_create_action_schema_uses_service_spec(self) -> None:
        schema = _generate()
        op = schema["paths"]["/authors/"]["post"]
        assert "201" in op["responses"]
        assert "422" in op["responses"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        assert body["$ref"].startswith("#/components/schemas/")

    def test_callable_success_status_documents_default(self) -> None:
        schema = _generate()
        op = schema["paths"]["/callable-status-create/"]["post"]
        # The callable can't be resolved statically → the create default stands.
        assert "201" in op["responses"]

    def test_list_action_schema_left_alone(self) -> None:
        schema = _generate()
        op = schema["paths"]["/authors/"]["get"]
        # Selector list reads ``output_serializer`` via ``ActionSerializerResolver``;
        # the AutoSchema does not attach a 422 to it.
        assert "200" in op["responses"]
        assert "422" not in op["responses"]


@pytest.mark.django_db
class TestServiceActionSchema:
    def test_action_emits_response_and_gates_out_no_input_422(self) -> None:
        schema = _generate()
        # ``approve`` is a detail action; spectacular places it under
        # /approvals/{id}/approve/.
        op = schema["paths"]["/approvals/{id}/approve/"]["post"]
        assert "200" in op["responses"]
        # The approve spec has no ``input_serializer``, so the 422 ServiceError
        # response is gated out — no spurious diff for a no-input action.
        assert "422" not in op["responses"]


@pytest.mark.django_db
class TestServiceErrorResponseGating:
    def test_no_input_delete_omits_422(self) -> None:
        schema = _generate()
        op = schema["paths"]["/plain-delete/{id}/"]["delete"]
        # No input_serializer → no spurious ServiceError response.
        assert "422" not in op["responses"]

    def test_input_bearing_mutation_keeps_422(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        assert "422" in op["responses"]

    def test_flag_true_forces_422_on_no_input(self) -> None:
        schema = _generate()
        op = schema["paths"]["/force-error-delete/{id}/"]["delete"]
        # document_service_error=True overrides the no-input heuristic.
        assert "422" in op["responses"]

    def test_flag_false_drops_422_on_input(self) -> None:
        schema = _generate()
        op = schema["paths"]["/no-error-create/"]["post"]
        # document_service_error=False overrides the input-present heuristic.
        assert "422" not in op["responses"]


class TestServiceErrorSerializer:
    def test_422_response_references_service_error(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        component_name = ref.rsplit("/", 1)[1]
        component = schema["components"]["schemas"][component_name]
        assert "detail" in component["properties"]


def _params(schema: dict[str, Any], route: str, method: str) -> list[dict[str, Any]]:
    return schema["paths"][route][method].get("parameters", [])


@pytest.mark.django_db
class TestFilterSetParameterParity:
    """Moving a FilterSet view→spec must leave the OpenAPI parameters unchanged."""

    def test_list_parameters_match_view_level_filterset(self) -> None:
        schema = _generate()
        view_level = _params(schema, "/posts-viewlevel/", "get")
        spec_level = _params(schema, "/posts-spec/", "get")
        # Byte-identical: same names, types, enums, ordering, style/explode.
        assert spec_level == view_level
        # And non-trivially present — the FilterSet actually contributed params.
        names = {p["name"] for p in spec_level}
        assert {"title", "published", "views", "kind", "labels", "order"} <= names

    def test_ordering_filter_shape(self) -> None:
        schema = _generate()
        order = {p["name"]: p for p in _params(schema, "/posts-spec/", "get")}["order"]
        assert order["in"] == "query"
        assert order["schema"]["type"] == "array"
        assert order["explode"] is False
        assert order["style"] == "form"
        # Ordering enum (asc + ``-`` desc variants) lives in the array items.
        assert "enum" in order["schema"]["items"]
        assert order.get("description")

    def test_multiple_choice_filter_shape(self) -> None:
        schema = _generate()
        labels = {p["name"]: p for p in _params(schema, "/posts-spec/", "get")}["labels"]
        assert labels["schema"]["type"] == "array"
        assert labels["explode"] is True
        assert labels["style"] == "form"

    def test_retrieve_parameters_match_and_omit_filters(self) -> None:
        # A detail operation documents no filter params in either config —
        # drf-spectacular gates filter params on list views — so parity holds
        # with both sides empty of filter params (only the path param remains).
        schema = _generate()
        view_level = _params(schema, "/post-viewlevel/{id}/", "get")
        spec_level = _params(schema, "/post-spec/{id}/", "get")
        assert spec_level == view_level
        assert "title" not in {p["name"] for p in spec_level}


class TestFilterSetParametersHelper:
    def test_noop_for_duck_typed_non_filterset(self) -> None:
        # A ``filter_set`` honouring only the ``(data, queryset) -> .qs``
        # contract (no ``base_filters``) degrades to no parameters.
        class _DuckFilter:
            def __init__(self, *, data: Any, queryset: Any) -> None: ...

            @property
            def qs(self) -> Any: ...

        assert _filter_set_parameters(ServiceAutoSchema(), _DuckFilter) == []

    def test_spec_filter_backend_returns_filter_set(self) -> None:
        sentinel = object()
        backend = _SpecFilterBackend(sentinel)
        assert backend.get_filterset_class(view=None, queryset=None) is sentinel
