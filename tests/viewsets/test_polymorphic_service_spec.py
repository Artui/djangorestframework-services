"""Integration: ``PolymorphicServiceSpec`` in a viewset ``action_specs``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    PolymorphicServiceSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceValidationError,
    ServiceViewSet,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _EmailIn:
    email: str


@dataclass
class _TokenIn:
    token: str


def _create_by_email(*, data: _EmailIn) -> Author:
    return Author.objects.create(name=f"email:{data.email}")


def _create_by_token(*, data: _TokenIn) -> Author:
    return Author.objects.create(name=f"token:{data.token}")


def _discriminate(*, data: Any) -> str:
    if "email" in data:
        return "email"
    if "token" in data:
        return "token"
    raise ServiceValidationError({"detail": "provide an email or token"})


def _refetch(*, result: Author) -> Any:
    return Author.objects.filter(pk=result.pk)


def _variant(service: Any, input_serializer: type) -> ServiceSpec:
    return ServiceSpec(
        service=service,
        input_serializer=input_serializer,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_refetch, output_serializer=AuthorSerializer
        ),
    )


_POLY = PolymorphicServiceSpec(
    discriminator=_discriminate,
    specs={
        "email": _variant(_create_by_email, _EmailIn),
        "token": _variant(_create_by_token, _TokenIn),
    },
)


class _PolyViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {"create": _POLY}


@pytest.mark.django_db
class TestPolymorphicDispatch:
    def test_routes_to_email_variant(self) -> None:
        view = _PolyViewSet.as_view({"post": "create"})
        response = view(factory.post("/", {"email": "a@b.c"}, format="json"))
        assert response.status_code == 201
        assert response.data["name"] == "email:a@b.c"

    def test_routes_to_token_variant(self) -> None:
        view = _PolyViewSet.as_view({"post": "create"})
        response = view(factory.post("/", {"token": "xyz"}, format="json"))
        assert response.status_code == 201
        assert response.data["name"] == "token:xyz"

    def test_rejected_payload_maps_to_400(self) -> None:
        view = _PolyViewSet.as_view({"post": "create"})
        response = view(factory.post("/", {"nope": 1}, format="json"))
        assert response.status_code == 400

    def test_unknown_variant_key_is_improperly_configured(self) -> None:
        poly = PolymorphicServiceSpec(
            discriminator=lambda: "missing",
            specs={"email": _variant(_create_by_email, _EmailIn)},
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        with pytest.raises(ImproperlyConfigured, match="not a configured variant"):
            _V.as_view({"post": "create"})(factory.post("/", {"email": "a@b.c"}, format="json"))

    def test_discriminator_runs_once_per_request(self) -> None:
        calls: list[int] = []

        def counting(*, data: Any) -> str:
            calls.append(1)
            return "email"

        poly = PolymorphicServiceSpec(
            discriminator=counting,
            specs={"email": _variant(_create_by_email, _EmailIn)},
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        # get_permissions (union) + dispatch both resolve the spec; the
        # discriminator (only reachable via the discriminate strategy or
        # dispatch) must not run twice for a single request.
        _V.as_view({"post": "create"})(factory.post("/", {"email": "a@b.c"}, format="json"))
        assert sum(calls) == 1


@pytest.mark.django_db
class TestPolymorphicValidation:
    def test_empty_specs_rejected(self) -> None:
        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": PolymorphicServiceSpec(discriminator=lambda: "x", specs={})}

        with pytest.raises(ImproperlyConfigured, match="must not be empty"):
            _V.as_view({"post": "create"})

    def test_non_service_variant_rejected(self) -> None:
        poly = PolymorphicServiceSpec(
            discriminator=lambda: "x",
            specs={"x": SelectorSpec(kind=SelectorKind.RETRIEVE)},  # type: ignore[dict-item]
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        with pytest.raises(ImproperlyConfigured, match="must be a ServiceSpec"):
            _V.as_view({"post": "create"})

    def test_require_identical_rejects_divergent_permissions(self) -> None:
        class _A(BasePermission): ...

        class _B(BasePermission): ...

        poly = PolymorphicServiceSpec(
            discriminator=lambda: "a",
            specs={
                "a": ServiceSpec(service=lambda: None, permission_classes=[_A]),
                "b": ServiceSpec(service=lambda: None, permission_classes=[_B]),
            },
            permission_strategy="require_identical",
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        with pytest.raises(ImproperlyConfigured, match="require_identical"):
            _V.as_view({"post": "create"})

    def test_require_identical_accepts_matching_permissions(self) -> None:
        class _A(BasePermission): ...

        poly = PolymorphicServiceSpec(
            discriminator=lambda: "a",
            specs={
                "a": ServiceSpec(service=lambda: None, permission_classes=[_A]),
                "b": ServiceSpec(service=lambda: None, permission_classes=[_A]),
            },
            permission_strategy="require_identical",
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        # Identical permission_classes → validation passes at as_view().
        assert _V.as_view({"post": "create"}) is not None


class _Deny(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


class _Allow(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True


@pytest.mark.django_db
class TestPolymorphicPermissions:
    def test_union_requires_all_variant_permissions(self) -> None:
        # One variant denies → the union denies regardless of which payload.
        poly = PolymorphicServiceSpec(
            discriminator=_discriminate,
            specs={
                "email": ServiceSpec(
                    service=_create_by_email, input_serializer=_EmailIn, permission_classes=[_Allow]
                ),
                "token": ServiceSpec(
                    service=_create_by_token, input_serializer=_TokenIn, permission_classes=[_Deny]
                ),
            },
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        # Even the email payload is denied because the union includes _Deny.
        response = _V.as_view({"post": "create"})(
            factory.post("/", {"email": "a@b.c"}, format="json")
        )
        assert response.status_code == 403

    def test_discriminate_applies_only_chosen_variant_permissions(self) -> None:
        poly = PolymorphicServiceSpec(
            discriminator=_discriminate,
            specs={
                "email": _variant(_create_by_email, _EmailIn),
                "token": ServiceSpec(
                    service=_create_by_token, input_serializer=_TokenIn, permission_classes=[_Deny]
                ),
            },
            permission_strategy="discriminate",
        )

        class _V(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"create": poly}

        view = _V.as_view({"post": "create"})
        # email variant (no deny) → allowed
        assert view(factory.post("/", {"email": "a@b.c"}, format="json")).status_code == 201
        # token variant (deny) → 403
        assert view(factory.post("/", {"token": "z"}, format="json")).status_code == 403


@pytest.mark.django_db
class TestPolymorphicServiceAction:
    def test_polymorphic_service_action_routes_by_payload(self) -> None:
        class _VS(GenericViewSet):
            queryset = Author.objects.all()

            @service_action(_POLY, detail=False, methods=["post"])
            def make(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        view = _VS.as_view({"post": "make"})
        assert view(factory.post("/", {"email": "x@y.z"}, format="json")).data["name"] == (
            "email:x@y.z"
        )
        assert view(factory.post("/", {"token": "t"}, format="json")).data["name"] == "token:t"
