"""A declared ``UrlKwarg`` the route does not capture is not written onto a live view.

Off HTTP, a transport's offline view carries no route, so the value a caller
sends for a declared ``UrlKwarg`` is put onto ``view.kwargs``, where a provider
reading the route finds it. A live DRF view's ``kwargs`` are its URLconf's, which
permission classes read as the author's: a body value written there would reach
``has_object_permission`` as though the route had captured it.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    UrlKwarg,
    dispatch_spec,
)
from rest_framework_services.types.offline_service_view import OfflineServiceView
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

_factory = APIRequestFactory()
SEEN: dict[str, Any] = {}


class _RecordsTheRoute(BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        SEEN["route"] = dict(view.kwargs)
        return True


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post, org: str = "none") -> Post:
    SEEN["org"] = org
    return instance


_SPEC = ServiceSpec(
    service=_touch,
    reads=(UrlKwarg("org"),),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer),
    atomic=False,
)


class _View(ServiceUpdateView):
    queryset = Post.objects.all()
    permission_classes = [_RecordsTheRoute]
    spec = _SPEC


@pytest.fixture(autouse=True)
def _reset() -> None:
    SEEN.clear()


@pytest.mark.django_db
def test_a_body_value_never_reaches_a_live_views_kwargs() -> None:
    post = Post.objects.create(title="p")

    response = _View.as_view()(_factory.put("/", {"org": "acme"}, format="json"), pk=post.pk)

    assert response.status_code == 200, response.data
    assert SEEN["route"] == {"pk": post.pk}
    assert SEEN["org"] == "acme"


@pytest.mark.django_db
def test_off_http_a_body_value_goes_onto_the_offline_views_kwargs() -> None:
    post = Post.objects.create(title="p")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})

    dispatch_spec(_SPEC, user=None, params={"org": "acme"}, view=view)

    assert view.kwargs == {"pk": post.pk, "org": "acme"}
    assert SEEN["org"] == "acme"
