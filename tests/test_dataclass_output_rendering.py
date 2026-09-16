"""A raw dataclass declared as ``output_serializer`` renders at every render site.

The schema side has always described such a declaration —
``output_to_json_schema`` walks a dataclass type field by field — while every
render site instantiated the declaration *as* a serializer, which for a dataclass
means calling its ``__init__`` with ``many=`` / ``context=`` and raising
``TypeError``. So a spec advertised a payload it could not produce.

Each test here drives one render site through the public surface only, so it
fails against a tree without the wrapping rather than dying at import. The
agreement tests at the bottom are the invariant the fix exists for: the payload
validates against the schema the package itself generates for the same
declaration, checked by a real JSON Schema validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import Any

import pytest
from django.test import override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from jsonschema import Draft202012Validator
from rest_framework import serializers
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
    arender_spec_output,
    render_spec_output,
    selector_action,
    spec_to_json_schema,
)
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.openapi import enable_openapi


@dataclass
class _Address:
    city: str
    postcode: str | None = None


@dataclass
class _Card:
    id: int
    name: str
    address: _Address
    nickname: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class _Price:
    amount: Decimal


def _ada() -> _Card:
    return _Card(id=1, name="Ada", address=_Address(city="London"), tags=["math"])


def _grace() -> _Card:
    return _Card(
        id=2,
        name="Grace",
        address=_Address(city="Arlington", postcode="22201"),
        nickname="Amazing",
    )


ADA: dict[str, Any] = {
    "id": 1,
    "name": "Ada",
    "address": {"city": "London", "postcode": None},
    "nickname": None,
    "tags": ["math"],
}
GRACE: dict[str, Any] = {
    "id": 2,
    "name": "Grace",
    "address": {"city": "Arlington", "postcode": "22201"},
    "nickname": "Amazing",
    "tags": [],
}


def _retrieve_spec() -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda **_: _ada(), output_serializer=_Card
    )


def _output_only() -> SelectorSpec[Any, Any]:
    """A service's nested output with no re-fetch, so the service's own result renders."""
    return SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Card)


def _list_spec() -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda **_: [_ada(), _grace()], output_serializer=_Card
    )


factory = APIRequestFactory()


# ----- the transport-neutral render step -----


class TestRenderSpecOutput:
    def test_single_object(self) -> None:
        assert render_spec_output(_retrieve_spec(), _ada()) == ADA

    def test_many(self) -> None:
        assert render_spec_output(_list_spec(), [_ada(), _grace()], many=True) == [ADA, GRACE]

    def test_a_service_spec_reads_its_nested_output(self) -> None:
        spec = ServiceSpec(service=lambda: _ada(), output_selector_spec=_output_only())
        assert render_spec_output(spec, _ada()) == ADA

    def test_a_mapping_renders_like_the_instance(self) -> None:
        """``DataclassSerializer`` reads by attribute or key, so a dict with the
        dataclass's shape renders the same as the dataclass itself."""
        payload = {"id": 1, "name": "Ada", "address": {"city": "London"}, "tags": ["math"]}
        assert render_spec_output(_retrieve_spec(), payload) == ADA


class TestARenderSpecOutput:
    async def test_single_object(self) -> None:
        assert await arender_spec_output(_retrieve_spec(), _ada()) == ADA

    async def test_many(self) -> None:
        rendered = await arender_spec_output(_list_spec(), [_ada(), _grace()], many=True)
        assert rendered == [ADA, GRACE]


# ----- the declared kinds that already rendered, unchanged -----


class _CardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class _ReadOnlyCard(serializers.BaseSerializer):
    """DRF's read-only pattern: no fields, only ``to_representation``."""

    def to_representation(self, instance: Any) -> Any:
        return {"label": f"{instance.name} (#{instance.id})"}


class TestOtherDeclarationsRenderAsBefore:
    def test_a_serializer_subclass(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, output_serializer=_CardSerializer)
        assert render_spec_output(spec, [_ada()], many=True) == [{"id": 1, "name": "Ada"}]

    def test_a_base_serializer_subclass(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_ReadOnlyCard)
        assert render_spec_output(spec, _ada()) == {"label": "Ada (#1)"}

    def test_no_output_passes_the_value_through(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE)
        card = _ada()
        assert render_spec_output(spec, card) is card

    def test_no_output_list_coerces_for_many(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST)
        cards = (card for card in [_ada(), _grace()])
        assert render_spec_output(spec, cards, many=True) == [_ada(), _grace()]


# ----- the HTTP read surfaces -----


class TestSelectorViews:
    def test_list_view(self) -> None:
        class _View(SelectorListView):
            spec = _list_spec()

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 200
        assert response.data == [ADA, GRACE]

    def test_retrieve_view(self) -> None:
        class _View(SelectorRetrieveView):
            spec = _retrieve_spec()

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 200
        assert response.data == ADA

    def test_retrieve_view_allow_none_renders_a_found_object(self) -> None:
        """The nullable-resource branch serializes through the same class."""

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda **_: _ada(),
                output_serializer=_Card,
                allow_none=True,
            )

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 200
        assert response.data == ADA


class TestViewSets:
    def test_list_action(self) -> None:
        class _View(SelectorViewSet):
            action_specs = {"list": _list_spec()}

        response = _View.as_view({"get": "list"})(factory.get("/"))
        assert response.status_code == 200
        assert response.data == [ADA, GRACE]

    def test_retrieve_action(self) -> None:
        class _View(SelectorViewSet):
            action_specs = {"retrieve": _retrieve_spec()}

        response = _View.as_view({"get": "retrieve"})(factory.get("/"), pk=1)
        assert response.status_code == 200
        assert response.data == ADA

    def test_a_service_action_resolves_a_renderable_class(self) -> None:
        """The ``ServiceSpec`` branch of ``get_serializer_class``, which anything
        calling ``get_serializer()`` under a mutation action instantiates."""

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(service=lambda: _ada(), output_selector_spec=_output_only())
            }

        view = _View()
        view.action = "create"
        serializer_class = view.get_serializer_class()
        assert issubclass(serializer_class, serializers.BaseSerializer)
        assert serializer_class(_ada()).data == ADA


class TestSelectorAction:
    class _View(GenericViewSet):
        @selector_action(_list_spec())
        def cards(self, request: Any) -> None: ...

        @selector_action(_retrieve_spec())
        def card(self, request: Any, pk: Any = None) -> None: ...

    def test_collection_action(self) -> None:
        response = self._View.as_view({"get": "cards"})(factory.get("/"))
        assert response.status_code == 200
        assert response.data == [ADA, GRACE]

    def test_detail_action(self) -> None:
        response = self._View.as_view({"get": "card"})(factory.get("/"), pk=1)
        assert response.status_code == 200
        assert response.data == ADA


# ----- a mutation's response -----


@dataclass
class _NameIn:
    name: str


def _create(*, data: _NameIn) -> _Card:
    return _Card(id=7, name=data.name, address=_Address(city="Paris"))


@pytest.mark.django_db
class TestMutationResponse:
    def test_single_instance_response(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create, input_serializer=_NameIn, output_selector_spec=_output_only()
            )

        response = _View.as_view()(factory.post("/", {"name": "Marie"}, format="json"))
        assert response.status_code == 201
        assert response.data == {
            "id": 7,
            "name": "Marie",
            "address": {"city": "Paris", "postcode": None},
            "nickname": None,
            "tags": [],
        }

    def test_bulk_response(self) -> None:
        """A ``many`` spec renders through ``render_spec_output`` rather than the
        single-instance renderer, so it is a separate site."""

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=lambda *, data: [_create(data=item) for item in data],
                input_serializer=_NameIn,
                many=True,
                output_selector_spec=_output_only(),
            )

        response = _View.as_view()(factory.post("/", [{"name": "a"}, {"name": "b"}], format="json"))
        assert response.status_code == 201
        assert [row["name"] for row in response.data] == ["a", "b"]
        assert response.data[0]["address"] == {"city": "Paris", "postcode": None}


# ----- OpenAPI for a read surface -----


def test_openapi_documents_a_selector_views_dataclass_output() -> None:
    """A read surface reaches drf-spectacular through ``get_serializer_class()``,
    which handed it the bare dataclass to instantiate."""

    class _View(SelectorRetrieveView):
        spec = _retrieve_spec()

    enable_openapi()
    schema = SchemaGenerator(patterns=[path("card/", _View.as_view())]).get_schema(
        request=None, public=True
    )
    component = schema["components"]["schemas"]["_Card"]
    assert set(component["properties"]) == {"id", "name", "address", "nickname", "tags"}


# ----- the cached wrapper freezes nothing -----


def test_settings_are_read_at_render_time_not_when_the_class_was_built() -> None:
    """The wrapper class is built once per dataclass, so this renders the same
    declaration twice under different settings: a class that captured the first
    render's settings would repeat its answer."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Price)
    assert render_spec_output(spec, _Price(amount=Decimal("1.50"))) == {"amount": "1.50"}
    with override_settings(REST_FRAMEWORK={"COERCE_DECIMAL_TO_STRING": False}):
        assert render_spec_output(spec, _Price(amount=Decimal("1.50"))) == {
            "amount": Decimal("1.50")
        }


# ----- the payload agrees with the schema the package advertises -----


def _assert_agrees(payload: Any, schema: Any) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    assert not errors, [error.message for error in errors]


class TestSchemaRenderAgreement:
    def test_item_keys_are_exactly_the_advertised_properties(self) -> None:
        """A validator alone would accept a payload missing an optional key, or
        carrying one the schema never named, so the key sets are compared too.

        The nested ``address`` is compared with its dataclass's own fields: the
        item schema leaves a nested dataclass open, so it has no keys to offer."""
        schema = output_to_json_schema(_Card)
        assert schema is not None
        for card in (_ada(), _grace()):
            payload = render_spec_output(_retrieve_spec(), card)
            assert set(payload) == set(schema["properties"])
            assert set(payload["address"]) == {f.name for f in fields(_Address)}

    def test_retrieve_payload_validates_against_its_schema(self) -> None:
        spec = _retrieve_spec()
        _assert_agrees(render_spec_output(spec, _ada()), spec_to_json_schema(spec, phase="output"))

    def test_optional_fields_validate_both_set_and_unset(self) -> None:
        """``nickname`` is ``None`` on one card and a string on the other, so both
        arms of its ``anyOf`` are exercised against the schema."""
        spec = _retrieve_spec()
        schema = spec_to_json_schema(spec, phase="output")
        unset = render_spec_output(spec, _ada())
        set_ = render_spec_output(spec, _grace())
        assert unset["nickname"] is None and set_["nickname"] == "Amazing"
        _assert_agrees(unset, schema)
        _assert_agrees(set_, schema)

    def test_list_payload_validates_against_its_schema(self) -> None:
        spec = _list_spec()
        payload = render_spec_output(spec, [_ada(), _grace()], many=True)
        _assert_agrees(payload, spec_to_json_schema(spec, phase="output"))
