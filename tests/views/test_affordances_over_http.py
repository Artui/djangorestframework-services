"""``affordances`` over HTTP: the status, the body, and what a denied caller learns.

Through the real views and read off the rendered response, because the wire is
what a client sees. The mapping is unit-tested next door; this is the half that
proves nothing between the dispatcher and the response flattens it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
)
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

factory = APIRequestFactory()

UNPUBLISHED = Affordance(
    code="already_published",
    reason="This post is already published and cannot be published again.",
    when=Q(published=False),
)


def _publish(*, instance: Post) -> Post:
    instance.published = True
    instance.save(update_fields=["published"])
    return instance


class _DenyObject(BasePermission):
    message = "You may not touch this post."

    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


def _view(**fields: Any) -> Any:
    class _PublishView(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(
            service=_publish,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
            ),
            affordances=[UNPUBLISHED],
            **fields,
        )

    return _PublishView.as_view()


def _body(response: Any) -> Any:
    response.render()
    return json.loads(response.content)


@pytest.mark.django_db
def test_an_unavailable_row_is_a_409_carrying_the_code_beside_the_detail() -> None:
    post = Post.objects.create(title="t", published=True)

    response = _view()(factory.patch("/", {}, format="json"), pk=post.pk)

    assert response.status_code == 409
    assert _body(response) == {
        "detail": "This post is already published and cannot be published again.",
        "code": "already_published",
    }


@pytest.mark.django_db
def test_an_available_row_is_served_as_before() -> None:
    post = Post.objects.create(title="t", published=False)

    response = _view()(factory.patch("/", {}, format="json"), pk=post.pk)

    assert response.status_code == 200
    assert _body(response)["published"] is True


@pytest.mark.django_db
def test_a_caller_denied_the_row_learns_nothing_about_its_state() -> None:
    post = Post.objects.create(title="t", published=True)

    response = _view(permission_classes=[_DenyObject])(
        factory.patch("/", {}, format="json"), pk=post.pk
    )

    assert response.status_code == 403
    body = _body(response)
    assert body == {"detail": "You may not touch this post."}
    assert "code" not in body
    assert "already published" not in json.dumps(body)


def test_a_row_condition_on_a_create_view_is_refused_at_as_view() -> None:
    class _CreateView(ServiceCreateView):
        spec = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED])

    with pytest.raises(ImproperlyConfigured, match=r"affordances\[0\] \('already_published'\)"):
        _CreateView.as_view()


def test_a_callable_condition_on_a_create_view_is_accepted() -> None:
    """The refusal is about needing a row; sized so only that can decide it."""

    class _CreateView(ServiceCreateView):
        spec = ServiceSpec(
            service=lambda: None,
            affordances=[Affordance(code="closed", reason="Closed.", when=lambda: True)],
        )

    _CreateView.as_view()
