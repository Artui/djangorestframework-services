"""OPTIONS against a spec-backed viewset.

DRF sets ``self.action = "metadata"`` for OPTIONS — an *implicit* action with no
handler method behind it. Anything that assumes ``self.action`` names a bound
method breaks there, and nothing in the suite exercised it, so the whole path
went unnoticed.

⚠ **Wider than explicit OPTIONS calls.** A CORS preflight is an OPTIONS request,
and ``django-cors-headers`` only short-circuits paths matching
``CORS_URLS_REGEX`` that also carry ``Access-Control-Request-Method`` — every
other route reaches the view. Easy to miss locally, easy to hit in production on
a subset of routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    SelectorViewSet,
    ServiceSpec,
    ServiceViewSet,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _AuthorIn:
    name: str


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return False


def _options(view: Any) -> Any:
    return view(factory.options("/"))


# ----- the defect -----


@pytest.mark.django_db
def test_options_on_a_service_viewset_does_not_raise() -> None:
    """⚠ The reported bug: ``AttributeError: … has no attribute 'metadata'``.

    ``get_permissions`` fell through to a ``getattr(self, self.action)`` with no
    default, looking for a handler that DRF documents as implicit. The
    ``AttributeError`` is not an ``APIException``, so ``handle_exception``
    re-raises it and the request ends as an unhandled 500 — before any
    permission was evaluated.
    """

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    assert _options(_View.as_view({"get": "list"})).status_code == 200


@pytest.mark.django_db
def test_options_on_a_selector_viewset_does_not_raise() -> None:
    class _View(SelectorViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    assert _options(_View.as_view({"get": "list"})).status_code == 200


@pytest.mark.django_db
def test_a_plain_drf_viewset_is_the_control() -> None:
    """Establishes that 200 is DRF's own behaviour, not something invented
    here — the mixin was the only thing standing between OPTIONS and it."""

    class _View(ViewSet):
        def list(self, request: Any) -> Any:  # pragma: no cover - never called
            raise AssertionError

    assert _options(_View.as_view({"get": "list"})).status_code == 200


# ----- with specs attached, which is the real deployment -----


@pytest.mark.django_db
def test_options_works_with_action_specs_declared() -> None:
    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(service=_create_author, input_serializer=AuthorSerializer)
        }

    assert _options(_View.as_view({"post": "create"})).status_code == 200


@pytest.mark.django_db
def test_options_works_with_a_selector_spec_declared() -> None:
    class _View(SelectorViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"list": SelectorSpec(kind=SelectorKind.LIST, selector=_list_authors)}

    assert _options(_View.as_view({"get": "list"})).status_code == 200


# ----- the permissions that do apply -----


@pytest.mark.django_db
def test_options_is_gated_by_the_views_own_permission_classes() -> None:
    """⚠ Fail-closed, and it must stay that way. A spec's
    ``permission_classes`` describe *that action*; ``metadata`` is not one, so
    the view's own classes are what OPTIONS answers to — which is exactly DRF's
    behaviour and not a hole opened by the fix."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        permission_classes = [_DenyAll]

    assert _options(_View.as_view({"get": "list"})).status_code in (401, 403)


@pytest.mark.django_db
def test_an_actions_spec_permissions_do_not_leak_into_options() -> None:
    """The converse: a locked-down ``create`` must not make OPTIONS deny, and a
    permissive one must not make it allow. They are unrelated questions."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(
                service=_create_author,
                input_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )
        }

    assert _options(_View.as_view({"post": "create"})).status_code == 200


@pytest.mark.django_db
def test_a_real_action_still_resolves_its_spec_permissions() -> None:
    """The guard must not have made ``get_permissions`` stop finding specs."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(
                service=_create_author,
                input_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )
        }

    response = _View.as_view({"post": "create"})(factory.post("/", {"name": "Ada"}))
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_options_answers_with_metadata_rather_than_an_empty_body() -> None:
    """It is a real DRF metadata response, not merely a non-500."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    response = _options(_View.as_view({"get": "list"}))
    assert "name" in response.data
    assert "renders" in response.data


# ----- the general shape of the guard -----


@pytest.mark.django_db
def test_any_action_without_a_bound_handler_is_survivable() -> None:
    """``metadata`` is the case DRF guarantees, but the branch was unsafe for
    *any* action name with no attribute behind it. Pinned generally so a future
    implicit action does not reopen this."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    view = _View()
    view.action = "not-a-method"
    view.request = None  # type: ignore[assignment]
    assert view.get_permissions() is not None
