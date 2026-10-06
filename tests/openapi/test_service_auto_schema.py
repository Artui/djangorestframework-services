"""End-to-end schema generation with drf-spectacular + ``ServiceAutoSchema``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import django_filters
import pytest
from django.urls import path
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    PolymorphicServiceSpec,
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


def _archive(*, instance: Author) -> Author:
    return instance


_ARCHIVED = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer)


class _ArchiveView(ServiceDeleteView):
    # A soft delete: the destroy presents the row it kept.
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_archive, output_selector_spec=_ARCHIVED, atomic=False)


class _ArchiveAt204View(ServiceDeleteView):
    # The same, with the 204 a destroy defaults to set explicitly.
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_archive, output_selector_spec=_ARCHIVED, success_status=204, atomic=False
    )


class _ArchiveAt202View(ServiceDeleteView):
    # Any other status carries the body as it is.
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_archive, output_selector_spec=_ARCHIVED, success_status=202, atomic=False
    )


class _DeletedCount(serializers.Serializer):
    deleted = serializers.IntegerField()


def _all_authors() -> Any:
    return Author.objects.all()


def _delete_counted(*, collection: Any) -> dict[str, int]:
    deleted, _ = collection.delete()
    return {"deleted": deleted}


def _delete_quietly(*, collection: Any) -> None:
    collection.delete()


_ALL_AUTHORS = SelectorSpec(kind=SelectorKind.LIST, selector=_all_authors)


class _BulkDeleteCountedView(ServiceDeleteView):
    # A bulk destroy presenting how many rows went.
    spec = ServiceSpec(
        service=_delete_counted,
        collection_selector_spec=_ALL_AUTHORS,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=_DeletedCount
        ),
        atomic=False,
    )


class _BulkDeleteView(ServiceDeleteView):
    spec = ServiceSpec(service=_delete_quietly, collection_selector_spec=_ALL_AUTHORS, atomic=False)


class _BulkDeleteCountedUndeclaredView(ServiceDeleteView):
    # The count again, with no serializer declaring it.
    spec = ServiceSpec(service=_delete_counted, collection_selector_spec=_ALL_AUTHORS, atomic=False)


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


@dataclass
class _EmailIn:
    email: str


@dataclass
class _TokenIn:
    token: str


def _create_email(*, data: _EmailIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.email)


def _create_token(*, data: _TokenIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.token)


_POLY_CREATE = PolymorphicServiceSpec(
    discriminator=lambda *, data: "email" if "email" in data else "token",
    specs={
        "email": ServiceSpec(service=_create_email, input_serializer=_EmailIn),
        "token": ServiceSpec(service=_create_token, input_serializer=_TokenIn),
    },
)


class _PolyViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {"create": _POLY_CREATE}


def _side_effect() -> None: ...


_POLY_NO_INPUT = PolymorphicServiceSpec(
    discriminator=lambda: "a",
    specs={"a": ServiceSpec(service=_side_effect), "b": ServiceSpec(service=_side_effect)},
)


class _PolyNoInputViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {"create": _POLY_NO_INPUT}


class _PolyActionViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @service_action(_POLY_CREATE, detail=False, methods=["post"], url_path="switch")
    def switch(self, request):  # type: ignore[no-untyped-def]
        ...


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
_router.register("poly", _PolyViewSet, basename="poly")
_router.register("poly-action", _PolyActionViewSet, basename="poly-action")
_router.register("poly-noinput", _PolyNoInputViewSet, basename="poly-noinput")

urlpatterns = [
    path("create/", _CreateView.as_view()),
    path("update/<int:pk>/", _UpdateView.as_view()),
    path("forced-full/<int:pk>/", _ForcedFullUpdateView.as_view()),
    path("delete/<int:pk>/", _DeleteView.as_view()),
    path("plain-delete/<int:pk>/", _DeletePlainView.as_view()),
    path("force-error-delete/<int:pk>/", _ForcedErrorDeleteView.as_view()),
    path("archive/<int:pk>/", _ArchiveView.as_view()),
    path("archive-at-204/<int:pk>/", _ArchiveAt204View.as_view()),
    path("archive-at-202/<int:pk>/", _ArchiveAt202View.as_view()),
    path("bulk-delete-counted/", _BulkDeleteCountedView.as_view()),
    path("bulk-delete/", _BulkDeleteView.as_view()),
    path("bulk-delete-undeclared/", _BulkDeleteCountedUndeclaredView.as_view()),
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


# Each destroy route as (its path, its view, the status it is served under).
_DESTROY_ROUTES: dict[str, tuple[str, Any, int]] = {
    "single-row presenting a row": ("/archive/{id}/", _ArchiveView, 200),
    "single-row presenting a row at an explicit 204": (
        "/archive-at-204/{id}/",
        _ArchiveAt204View,
        200,
    ),
    "single-row presenting a row at an explicit 202": (
        "/archive-at-202/{id}/",
        _ArchiveAt202View,
        202,
    ),
    "single-row presenting nothing": ("/plain-delete/{id}/", _DeletePlainView, 204),
    "bulk presenting a count": ("/bulk-delete-counted/", _BulkDeleteCountedView, 200),
    "bulk presenting nothing": ("/bulk-delete/", _BulkDeleteView, 204),
}


@pytest.mark.django_db
@pytest.mark.parametrize("route", list(_DESTROY_ROUTES))
def test_a_destroy_documents_the_response_it_serves(route: str) -> None:
    """The schema and the runtime agree on a destroy's success response: its one
    documented 2xx is the status the route answers, and it documents a body
    exactly where the route sends one.

    A destroy presenting a value answers ``200``, because a ``204`` carries no
    body, and the schema used to document the serializer under the ``204``.
    """
    path, view, served = _DESTROY_ROUTES[route]
    documented = _generate()["paths"][path]["delete"]["responses"]
    pk = Author.objects.create(name="a").pk
    kwargs = {"pk": pk} if "{id}" in path else {}
    response = view.as_view()(APIRequestFactory().delete("/"), **kwargs)

    assert response.status_code == served
    assert [code for code in documented if code.startswith("2")] == [str(served)]
    assert ("content" in documented[str(served)]) is (response.data is not None)


@pytest.mark.django_db
def test_an_undeclared_body_is_documented_as_no_content() -> None:
    """The limit the OpenAPI page states, pinned so it stays true.

    With no ``output_serializer``, only what the service returns decides whether
    there is a body. The schema documents the empty ``204`` a destroy returning
    ``None`` is answered with, while this one's count is served as a ``200``.
    """
    documented = _generate()["paths"]["/bulk-delete-undeclared/"]["delete"]["responses"]
    Author.objects.create(name="a")
    response = _BulkDeleteCountedUndeclaredView.as_view()(APIRequestFactory().delete("/"))

    assert list(documented) == ["204"]
    assert "content" not in documented["204"]
    assert (response.status_code, response.data) == (200, {"deleted": 1})


def _create_author() -> Author:
    return Author.objects.create(name="x")


def _no_author_visible(*, result: Author) -> Any:
    # A re-read scoped to what the caller may see, which here is nothing.
    return Author.objects.none()


def _the_created_author(*, result: Author) -> Any:
    return Author.objects.filter(pk=result.pk)


def _create_view(output_selector_spec: SelectorSpec[Any, Any]) -> Any:
    spec = ServiceSpec(
        service=_create_author, output_selector_spec=output_selector_spec, atomic=False
    )
    return type("_CreateReReading", (ServiceCreateView,), {"spec": spec})


def _documented_success(view: Any) -> dict[str, Any]:
    generator = SchemaGenerator(patterns=[path("c/", view.as_view())])
    responses = generator.get_schema(request=None, public=True)["paths"]["/c/"]["post"]
    return {code: body for code, body in responses["responses"].items() if code.startswith("2")}


@pytest.mark.django_db
def test_a_create_that_may_present_nothing_documents_its_empty_204() -> None:
    """A re-read that finds no row is answered with an empty ``204``, not the
    ``201`` the serializer is documented under, so the schema documents both."""
    view = _create_view(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_no_author_visible,
            output_serializer=AuthorSerializer,
        )
    )

    documented = _documented_success(view)
    response = view.as_view()(APIRequestFactory().post("/", {}, format="json"))

    assert response.status_code == 204
    assert sorted(documented) == ["201", "204"]
    assert "content" in documented["201"]
    assert "content" not in documented["204"]


@pytest.mark.django_db
def test_a_create_that_cannot_present_nothing_documents_no_204() -> None:
    """Nothing to re-read and no ``allow_none``: the service's own row is presented."""
    view = _create_view(
        SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer)
    )

    assert sorted(_documented_success(view)) == ["201"]


@pytest.mark.django_db
def test_an_undeclared_body_is_documented_at_its_status_alone() -> None:
    """The limit the OpenAPI page states: with no ``output_serializer`` the schema
    documents one empty response at the status, whether or not the re-read may
    find nothing, because whether a body goes out is not declared anywhere it
    reads."""
    view = _create_view(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_the_created_author))

    documented = _documented_success(view)

    assert sorted(documented) == ["201"]
    assert "content" not in documented["201"]


# --- where the documented ``204`` is not the empty answer served -----------------
#
# The schema documents the empty ``204`` wherever ``can_present_nothing`` says
# dispatch may present ``None``. The renderers decide the empty answer from more
# than the spec: an explicit ``success_status``, whether the view renders an
# update's target in place, and whether the spec is answered by the bulk path. The
# OpenAPI page states each case, and each test below puts the status served beside
# the statuses documented, so a schema that grows exact fails here with the page.


def _returns_nothing(**_: Any) -> None:
    return None


_PRESENTS_ITS_OWN = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer)


def _documented_at(view: Any, route: str, path_key: str, method: str) -> list[str]:
    generator = SchemaGenerator(patterns=[path(route, view.as_view())])
    operation = generator.get_schema(request=None, public=True)["paths"][path_key][method]
    return sorted(code for code in operation["responses"] if code.startswith("2"))


@pytest.mark.django_db
def test_an_empty_answer_at_an_explicit_status_is_documented_as_204_too() -> None:
    """With nothing to re-read, an explicit ``success_status`` is the status the
    empty answer goes out under, so the ``204`` documented is never served."""
    spec = ServiceSpec(
        service=_returns_nothing,
        output_selector_spec=_PRESENTS_ITS_OWN,
        allow_none=True,
        success_status=201,
        atomic=False,
    )
    view = type("_CreateAt201", (ServiceCreateView,), {"spec": spec})

    response = view.as_view()(APIRequestFactory().post("/", {}, format="json"))

    assert (response.status_code, response.data) == (201, None)
    assert _documented_at(view, "c/", "/c/", "post") == ["201", "204"]


@pytest.mark.django_db
def test_a_re_read_finding_nothing_answers_204_whatever_the_status() -> None:
    """The contrast the page draws: a re-read's ``None`` is authoritative, so it is
    answered with the ``204`` documented, at an explicit ``201`` too."""
    spec = ServiceSpec(
        service=_create_author,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_no_author_visible,
            output_serializer=AuthorSerializer,
        ),
        success_status=201,
        atomic=False,
    )
    view = type("_ReReadAt201", (ServiceCreateView,), {"spec": spec})

    response = view.as_view()(APIRequestFactory().post("/", {}, format="json"))

    assert (response.status_code, response.data) == (204, None)
    assert _documented_at(view, "c/", "/c/", "post") == ["201", "204"]


@pytest.mark.django_db
def test_an_update_rendering_its_target_in_place_is_documented_with_a_204() -> None:
    """An update whose service returns ``None`` renders the row it updated, so it
    never answers empty, while ``allow_none`` has the schema document the ``204``."""
    author = Author.objects.create(name="x")
    spec = ServiceSpec(
        service=_returns_nothing,
        output_selector_spec=_PRESENTS_ITS_OWN,
        allow_none=True,
        atomic=False,
    )
    view = type(
        "_UpdateInPlace", (ServiceUpdateView,), {"spec": spec, "queryset": Author.objects.all()}
    )

    response = view.as_view()(APIRequestFactory().put("/", {}, format="json"), pk=author.pk)

    assert (response.status_code, response.data) == (200, {"id": author.pk, "name": "x"})
    assert _documented_at(view, "u/<int:pk>/", "/u/{id}/", "put") == ["200", "204"]


class _TouchViewSet(ServiceViewSet):
    queryset = Author.objects.all()

    @service_action(
        ServiceSpec(
            service=_returns_nothing,
            output_selector_spec=_PRESENTS_ITS_OWN,
            allow_none=True,
            atomic=False,
        ),
        detail=True,
        methods=["post"],
    )
    def touch(self, request: Any, pk: Any = None) -> Any: ...


@pytest.mark.django_db
def test_a_detail_action_rendering_its_target_in_place_is_documented_with_a_204() -> None:
    """A detail ``@service_action`` renders its target as an update does."""
    author = Author.objects.create(name="x")
    router = DefaultRouter()
    router.register("touch", _TouchViewSet, basename="touch")
    view = _TouchViewSet.as_view({"post": "touch"})

    response = view(APIRequestFactory().post("/", {}, format="json"), pk=author.pk)
    schema = SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)
    operation = schema["paths"]["/touch/{id}/touch/"]["post"]

    assert (response.status_code, response.data) == (200, {"id": author.pk, "name": "x"})
    assert sorted(code for code in operation["responses"] if code.startswith("2")) == [
        "200",
        "204",
    ]


def _none_visible(*, result: Any) -> Any:
    return Author.objects.none()


@pytest.mark.django_db
def test_a_bulk_spec_answers_empty_at_its_status_beside_a_documented_204() -> None:
    """A collection target is answered by the bulk path, which sends an empty answer
    under the action's status, never ``204`` unless that is the status. A re-read
    finding no row is answered ``200`` here, as an update, beside a documented
    ``204``."""
    Author.objects.create(name="x")
    spec = ServiceSpec(
        service=_returns_nothing,
        collection_selector_spec=_ALL_AUTHORS,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_none_visible, output_serializer=AuthorSerializer
        ),
        atomic=False,
    )
    view = type(
        "_BulkUpdate", (ServiceUpdateView,), {"spec": spec, "queryset": Author.objects.all()}
    )

    response = view.as_view()(APIRequestFactory().put("/", {}, format="json"), pk=0)

    assert (response.status_code, response.data) == (200, None)
    assert _documented_at(view, "u/<int:pk>/", "/u/{id}/", "put") == ["200", "204"]


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

    def _poly_request_component(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        body = schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        # The PolymorphicProxySerializer is registered as a named component; the
        # request body $refs it and the oneOf lives inside.
        ref = body["$ref"]
        return schema["components"]["schemas"][ref.rsplit("/", 1)[1]]

    def test_polymorphic_action_request_body_is_variant_union(self) -> None:
        schema = _generate()
        component = self._poly_request_component(schema, "/poly/")
        # PolymorphicProxySerializer(resource_type_field_name=None) → a oneOf of
        # the variant input serializers, no discriminator field.
        assert "oneOf" in component
        assert len(component["oneOf"]) == 2

    def test_polymorphic_service_action_request_body_is_variant_union(self) -> None:
        schema = _generate()
        component = self._poly_request_component(schema, "/poly-action/switch/")
        assert "oneOf" in component
        assert len(component["oneOf"]) == 2

    def test_polymorphic_without_variant_input_serializers_falls_back(self) -> None:
        # No variant declares an input_serializer → no proxy is built and the
        # base body applies (no request body for a no-input mutation).
        schema = _generate()
        op = schema["paths"]["/poly-noinput/"]["post"]
        assert "requestBody" not in op

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
