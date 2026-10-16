"""A deactivated user acting through a live view is refused with a 403.

The kernel refuses a principal whose ``is_active`` is false before any
permission check runs, so a view declaring no ``permission_classes`` at all
refuses it too, where 0.57.0 ran the service. The refusal is
``PrincipalUnavailable``: ``{"detail": "The acting principal is unavailable."}``,
code ``permission_denied``. The same user, active, is served, so each test
holds the flag rather than the view.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from rest_framework_services import (
    SelectorKind,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
)
from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.service_specs.principal_unavailable import PrincipalUnavailable
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()
_UNAVAILABLE = {"detail": "The acting principal is unavailable."}
_ACTIVE = pytest.mark.parametrize("active", [True, False], ids=["active", "deactivated"])

ran: list[str] = []


@pytest.fixture(autouse=True)
def _forget_runs() -> None:
    ran.clear()


def _user(active: bool) -> Any:
    return get_user_model().objects.create_user(username="u", is_active=active)


def _as(request: Any, active: bool) -> Any:
    force_authenticate(request, user=_user(active))
    return request


def _create(*, data: Any) -> Author:
    ran.append("create")
    return Author.objects.create(name=data["name"])


def _update(*, instance: Author, data: Any) -> Author:
    ran.append("update")
    instance.name = data["name"]
    instance.save()
    return instance


def _delete(*, instance: Author) -> None:
    ran.append("delete")
    instance.delete()


async def _acreate(*, data: Any) -> Author:
    ran.append("acreate")
    return await Author.objects.acreate(name=data["name"])


def _read(*, pk: int) -> Any:
    ran.append("read")
    return Author.objects.filter(pk=pk)


class _Create(ServiceCreateView):
    permission_classes: Any = []
    spec = ServiceSpec(service=_create, input_serializer=AuthorSerializer)


class _ACreate(ServiceCreateView):
    permission_classes: Any = []
    spec = ServiceSpec(service=_acreate, input_serializer=AuthorSerializer)


class _Update(ServiceUpdateView):
    permission_classes: Any = []
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_update, input_serializer=AuthorSerializer)


class _Delete(ServiceDeleteView):
    permission_classes: Any = []
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete)


class _Retrieve(SelectorRetrieveView):
    permission_classes: Any = []
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_read, output_serializer=AuthorSerializer
    )


class _ViewSet(ServiceViewSet):
    permission_classes: Any = []
    queryset = Author.objects.all()
    action_specs = {"create": ServiceSpec(service=_create, input_serializer=AuthorSerializer)}


def _answered(response: Any, active: bool, status: int, service: str) -> None:
    if active:
        assert response.status_code == status, response.data
        assert ran == [service]
    else:
        assert response.status_code == 403
        assert response.data == _UNAVAILABLE
        assert response.data["detail"].code == "permission_denied"
        assert ran == []


@_ACTIVE
def test_a_create_view_with_no_permission_classes(active: bool) -> None:
    request = _as(_factory.post("/", {"name": "n"}, format="json"), active)
    _answered(_Create.as_view()(request), active, 201, "create")


@_ACTIVE
def test_an_async_service_behind_a_create_view(active: bool) -> None:
    request = _as(_factory.post("/", {"name": "n"}, format="json"), active)
    _answered(_ACreate.as_view()(request), active, 201, "acreate")


@_ACTIVE
def test_an_update_view_with_no_permission_classes(active: bool) -> None:
    author = Author.objects.create(name="a")
    request = _as(_factory.put("/", {"name": "n"}, format="json"), active)
    _answered(_Update.as_view()(request, pk=author.pk), active, 200, "update")


@_ACTIVE
def test_a_delete_view_with_no_permission_classes(active: bool) -> None:
    author = Author.objects.create(name="a")
    request = _as(_factory.delete("/"), active)
    _answered(_Delete.as_view()(request, pk=author.pk), active, 204, "delete")


@_ACTIVE
def test_a_retrieve_view_with_no_permission_classes(active: bool) -> None:
    author = Author.objects.create(name="a")
    request = _as(_factory.get("/"), active)
    _answered(_Retrieve.as_view()(request, pk=author.pk), active, 200, "read")


@_ACTIVE
def test_a_viewset_action_with_no_permission_classes(active: bool) -> None:
    request = _as(_factory.post("/", {"name": "n"}, format="json"), active)
    _answered(_ViewSet.as_view({"post": "create"})(request), active, 201, "create")


@pytest.mark.django_db(transaction=True)
async def test_the_async_core_refuses_it_on_a_live_view() -> None:
    user = await get_user_model().objects.acreate(username="u", is_active=False)
    view = ServiceCreateView()
    view.request = Request(_factory.post("/", {"name": "n"}, format="json"), parsers=[JSONParser()])
    view.request.user = user
    view.kwargs = {}
    view.format_kwarg = None

    with pytest.raises(PrincipalUnavailable):
        await adispatch_spec(
            ServiceSpec(service=_acreate, input_serializer=AuthorSerializer),
            user=user,
            params=view.request.data,
            request=view.request,
            view=view,
        )
    assert ran == []
