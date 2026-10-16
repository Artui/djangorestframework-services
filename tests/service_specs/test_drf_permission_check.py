"""``DRFPermissionCheck``: a drfs spec's ``permission_classes`` asked through the kernel.

Every case hands the drfs spec itself to the kernel's ``authorize`` or
``dispatch``, so the kernel's own reading of the ``arguments`` opt-in is what is
tested, not a direct call that would pass whether or not the kernel ever
handed the arguments over.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.dispatch.authorize import authorize
from django_service_specs.dispatch.dispatch import dispatch
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from rest_framework_services.service_specs.not_permitted import NotPermitted
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Post

_SEEN: list[dict[str, Any]] = []


class _OnlySeven(BasePermission):
    """Admits the call whose route captured ``pk`` 7, as a nested-route permission does."""

    message = "Only row seven."
    code = "not_seven"

    def has_permission(self, request: Any, view: Any) -> bool:
        return view.kwargs.get("pk") == 7


class _Records(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        _SEEN.append(
            {
                "kwargs": dict(view.kwargs),
                "query": dict(request.query_params.lists()),
                "user": request.user,
            }
        )
        return True


class _NoObjects(BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


class _RaisesOnAnyObject(BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        raise AssertionError(f"asked about {obj!r}, which DRF never asks about")


def _lookup(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _update(*, instance: Any) -> Any:
    return instance


def _create() -> dict[str, bool]:
    return {"created": True}


@pytest.fixture(autouse=True)
def _clear() -> None:
    _SEEN.clear()


def _recorded() -> dict[str, Any]:
    (seen,) = _SEEN
    return seen


class TestTheRouteIsFilledBeforeTheClassLevelCheck:
    def test_a_class_reading_view_kwargs_sees_the_lookup_s_argument(self) -> None:
        spec = ServiceSpec(
            service=_update,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
            permission_classes=[_OnlySeven],
        )

        authorize(spec, AnonymousUser(), arguments={"pk": 7})
        with pytest.raises(NotPermitted):
            authorize(spec, AnonymousUser(), arguments={"pk": 8})

    def test_a_retrieve_s_own_reads_are_its_route(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, permission_classes=[_OnlySeven]
        )

        authorize(spec, AnonymousUser(), arguments={"pk": 7})

    def test_a_list_s_reads_are_not_its_route(self) -> None:
        def filtered(*, status: str) -> Any:
            return Post.objects.all()

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=filtered, permission_classes=[_Records]
        )

        authorize(spec, AnonymousUser(), arguments={"status": "open"})

        assert _recorded()["kwargs"] == {}

    def test_a_declared_url_kwarg_reaches_view_kwargs(self) -> None:
        spec = ServiceSpec(
            service=_create, permission_classes=[_Records], reads=[UrlKwarg("project_pk")]
        )

        authorize(spec, AnonymousUser(), arguments={"project_pk": "4"})

        assert _recorded()["kwargs"] == {"project_pk": "4"}

    @pytest.mark.parametrize("arguments", [{}, {"project_pk": None}], ids=["absent", "null"])
    def test_an_omitted_url_kwarg_takes_its_default(self, arguments: dict[str, Any]) -> None:
        spec = ServiceSpec(
            service=_create,
            permission_classes=[_Records],
            reads=[UrlKwarg("project_pk", default="1"), UrlKwarg("team_pk")],
        )

        authorize(spec, AnonymousUser(), arguments=arguments)

        assert _recorded()["kwargs"] == {"project_pk": "1"}

    def test_a_declared_query_param_reaches_the_query_string_not_the_route(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_lookup,
            permission_classes=[_Records],
            reads=[QueryParam("fields")],
        )

        authorize(spec, AnonymousUser(), arguments={"pk": 3, "fields": "title"})

        assert _recorded()["kwargs"] == {"pk": 3}
        assert _recorded()["query"] == {"fields": ["title"]}

    def test_a_url_kwarg_outranks_a_lookup_read_of_its_name(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_lookup,
            permission_classes=[_Records],
            reads=[UrlKwarg("pk", type="integer", default=5)],
        )

        authorize(spec, AnonymousUser(), arguments={})

        assert _recorded()["kwargs"] == {"pk": 5}

    def test_no_arguments_fills_nothing(self) -> None:
        spec = ServiceSpec(
            service=_create,
            permission_classes=[_Records],
            reads=[UrlKwarg("project_pk", default="1")],
        )

        authorize(spec, AnonymousUser())

        assert _recorded()["kwargs"] == {}

    def test_the_principal_is_the_request_s_user(self) -> None:
        principal = AnonymousUser()
        spec = ServiceSpec(service=_create, permission_classes=[_Records])

        authorize(spec, principal, arguments={})

        assert _recorded()["user"] is principal


class TestARefusal:
    def test_carries_the_class_s_message_and_code_as_both_bases(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, permission_classes=[_OnlySeven]
        )

        with pytest.raises(NotPermitted) as refused:
            authorize(spec, AnonymousUser(), arguments={"pk": 1})

        assert isinstance(refused.value, KernelNotPermitted)
        assert isinstance(refused.value, PermissionDenied)
        assert refused.value.message == "Only row seven."
        assert refused.value.get_codes() == "not_seven"

    def test_the_first_refusing_class_answers_and_the_rest_are_not_asked(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_lookup,
            permission_classes=[_OnlySeven, _Records],
        )

        with pytest.raises(NotPermitted):
            authorize(spec, AnonymousUser(), arguments={"pk": 1})

        assert _SEEN == []


@pytest.mark.django_db
class TestTheObjectCheck:
    def test_a_model_row_is_asked_about(self) -> None:
        post = Post.objects.create(title="t")
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, permission_classes=[_NoObjects]
        )

        with pytest.raises(NotPermitted):
            dispatch(spec, principal=AnonymousUser(), arguments={"pk": post.pk})

    def test_a_model_row_every_class_admits_is_returned(self) -> None:
        post = Post.objects.create(title="t")
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, permission_classes=[_Records]
        )

        result = dispatch(spec, principal=AnonymousUser(), arguments={"pk": post.pk})

        assert result.value == post

    def test_a_target_that_is_no_model_is_not_asked_about(self) -> None:
        def row(*, pk: int) -> dict[str, int]:
            return {"pk": pk}

        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=row, permission_classes=[_NoObjects]
        )

        result = dispatch(spec, principal=AnonymousUser(), arguments={"pk": 2})

        assert result.value == {"pk": 2}


@pytest.mark.django_db
class TestTheScopeCheck:
    def test_a_create_is_never_asked_about_an_object(self) -> None:
        spec = ServiceSpec(service=_create, permission_classes=[_RaisesOnAnyObject])

        result = dispatch(spec, principal=AnonymousUser(), arguments={})

        assert result.value == {"created": True}

    def test_a_collection_is_never_asked_about_an_object(self) -> None:
        Post.objects.create(title="t")

        def every(*, user: Any) -> Any:
            return Post.objects.all()

        def touch(*, collection: Any) -> int:
            return collection.count()

        spec = ServiceSpec(
            service=touch,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=every),
            permission_classes=[_RaisesOnAnyObject],
        )

        result = dispatch(spec, principal=AnonymousUser(), arguments={})

        assert result.value == 1
