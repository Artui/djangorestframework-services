"""A refusal comes back under the names the request used, not the model's.

A service raises about the model, because the model is what it was handed. The
request may have called those fields something else -- a serializer field
declares ``source=`` precisely to let the two diverge -- and DRF resolves
``source=`` while building ``validated_data``, at every depth, so by the time
the service runs the wire name is gone from the payload entirely. The serializer
is the one thing still holding both vocabularies, and the dispatcher has it in
scope when the service is called.

These assert the rename reaches every depth and every transport, that it leaves
alone what it cannot name, and that a serializer field which cannot correspond
to an input key is never used as a source of names.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ChildSpec,
    ServiceSpec,
    ServiceValidationError,
    ServiceViewSet,
    adispatch_spec,
    create_from_input,
    dispatch_spec,
)
from rest_framework_services.dispatch.utils import _wire_names
from tests.testapp.models import Catalog, Post, Section


class _AuthorInput(serializers.Serializer):
    some_name = serializers.CharField(source="name")


class _SectionInput(serializers.Serializer):
    heading = serializers.CharField(source="title")


class _PostInput(serializers.Serializer):
    headline = serializers.CharField(source="title")
    writer = _AuthorInput(source="author")


class _CatalogInput(serializers.Serializer):
    name = serializers.CharField()
    chapters = _SectionInput(source="sections", many=True)


def _refuse(detail: Any) -> Any:
    """A service that refuses with ``detail``, in model vocabulary."""

    def service(**_: Any) -> None:
        raise ServiceValidationError(detail)

    return service


def _arefuse(detail: Any) -> Any:
    """The async twin of :func:`_refuse`."""

    async def service(**_: Any) -> None:
        raise ServiceValidationError(detail)

    return service


def _create_view(spec: ServiceSpec[Any, Any, Any]) -> Any:
    viewset = type(
        "_VS",
        (ServiceViewSet,),
        {"queryset": Post.objects.all(), "action_specs": {"create": spec}},
    )
    return viewset.as_view({"post": "create"})


_PAYLOAD: dict[str, Any] = {"headline": "t", "writer": {"some_name": "x"}}


@pytest.mark.django_db
class TestTheRenameReachesEveryDepth:
    def test_a_top_level_field_takes_its_wire_name(self) -> None:
        spec = ServiceSpec(service=_refuse({"title": ["Too long."]}), input_serializer=_PostInput)

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"headline": ["Too long."]}

    def test_a_nested_field_takes_its_own(self) -> None:
        """The half a relation-level alias could never reach."""
        spec = ServiceSpec(
            service=_refuse({"author": {"name": ["Too short."]}}), input_serializer=_PostInput
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"writer": {"some_name": ["Too short."]}}

    def test_a_collection_is_renamed_without_losing_its_alignment(self) -> None:
        spec = ServiceSpec(
            service=_refuse({"sections": [{}, {"title": ["Too rude."]}]}),
            input_serializer=_CatalogInput,
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(
                spec,
                user=None,
                params={"name": "c", "chapters": [{"heading": "a"}, {"heading": "b"}]},
            )

        assert excinfo.value.detail == {"chapters": [{}, {"heading": ["Too rude."]}]}

    def test_it_meets_a_real_nested_write(self) -> None:
        """End to end: the relation spec names the model, the caller sees the wire."""

        def service(*, data: Any) -> Any:
            return create_from_input(
                Catalog,
                data,
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        create_service=_refuse({"title": ["Too rude."]}),
                    )
                },
            ).instance

        spec = ServiceSpec(service=service, input_serializer=_CatalogInput)

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params={"name": "c", "chapters": [{"heading": "a"}]})

        assert excinfo.value.detail == {"chapters": [{"heading": ["Too rude."]}]}


@pytest.mark.django_db
class TestWhatItLeavesAlone:
    def test_a_key_the_serializer_does_not_know(self) -> None:
        """Renaming what it can name means never guessing at what it cannot."""
        spec = ServiceSpec(
            service=_refuse({"non_field_errors": ["Nope."], "title": ["Too long."]}),
            input_serializer=_PostInput,
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {
            "non_field_errors": ["Nope."],
            "headline": ["Too long."],
        }

    def test_a_detail_that_is_not_a_field_map(self) -> None:
        """A service may raise a string; there is no field name in it to rename."""
        spec = ServiceSpec(service=_refuse("Nope."), input_serializer=_PostInput)

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == "Nope."

    def test_a_list_of_messages_under_a_renamed_key(self) -> None:
        """The key is renamed; the messages under it are not walked into."""
        spec = ServiceSpec(
            service=_refuse({"author": ["Unavailable."]}), input_serializer=_PostInput
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"writer": ["Unavailable."]}

    def test_a_spec_with_no_input_serializer(self) -> None:
        """No second vocabulary, so nothing to translate between."""
        spec = ServiceSpec(service=_refuse({"title": ["Too long."]}))

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params={})

        assert excinfo.value.detail == {"title": ["Too long."]}

    def test_a_service_that_does_not_raise(self) -> None:
        spec = ServiceSpec(service=lambda **_: {"ok": True}, input_serializer=_PostInput)

        assert dispatch_spec(spec, user=None, params=_PAYLOAD).value == {"ok": True}


class TestWhichFieldsAreAsked:
    """A field that cannot correspond to an input key is not a source of names."""

    def test_a_read_only_field_does_not_shadow_the_writable_one(self) -> None:
        class _Shadowed(serializers.Serializer):
            display = serializers.CharField(source="title", read_only=True)
            headline = serializers.CharField(source="title")

        assert _wire_names(_Shadowed())["title"][0] == "headline"

    def test_a_dotted_source_is_skipped(self) -> None:
        """``source="author.name"`` is not a key of ``validated_data``."""

        class _Dotted(serializers.Serializer):
            author_name = serializers.CharField(source="author.name")

        assert _wire_names(_Dotted()) == {}

    def test_a_whole_object_source_is_skipped(self) -> None:
        class _Star(serializers.Serializer):
            everything = serializers.CharField(source="*")

        assert _wire_names(_Star()) == {}

    def test_two_writable_fields_on_one_source_take_the_first(self) -> None:
        """Not a shape DRF can save, so it is settled rather than guessed at."""

        class _Twice(serializers.Serializer):
            first = serializers.CharField(source="title")
            second = serializers.CharField(source="title")

        assert _wire_names(_Twice())["title"][0] == "first"

    def test_a_plain_field_contributes_no_nested_map(self) -> None:
        assert _wire_names(_PostInput())["title"] == ("headline", None)

    def test_a_many_field_is_read_through_its_child(self) -> None:
        assert _wire_names(_CatalogInput())["sections"] == (
            "chapters",
            {"title": ("heading", None)},
        )


@pytest.mark.django_db
class TestEveryTransportAndBothErrorClasses:
    def test_it_applies_over_http(self) -> None:
        spec = ServiceSpec(
            service=_refuse({"author": {"name": ["Too short."]}}), input_serializer=_PostInput
        )

        response = _create_view(spec)(APIRequestFactory().post("/x/", _PAYLOAD, format="json"))

        assert response.status_code == 400
        assert response.data == {"writer": {"some_name": ["Too short."]}}

    async def test_it_applies_on_the_async_path(self) -> None:
        spec = ServiceSpec(
            service=_arefuse({"author": {"name": ["Too short."]}}), input_serializer=_PostInput
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            await adispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"writer": {"some_name": ["Too short."]}}

    def test_drf_s_own_error_class_is_preserved(self) -> None:
        def service(**_: Any) -> None:
            raise ValidationError({"title": ["Too long."]})

        spec = ServiceSpec(service=service, input_serializer=_PostInput)

        with pytest.raises(ValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"headline": ["Too long."]}

    def test_a_precondition_is_renamed_too(self) -> None:
        """Preconditions speak about the input, so they owe the same names."""

        def refuse_precondition(**_: Any) -> None:
            raise ServiceValidationError({"title": ["Locked."]})

        spec = ServiceSpec(
            service=lambda **_: None,
            input_serializer=_PostInput,
            preconditions=[refuse_precondition],
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=_PAYLOAD)

        assert excinfo.value.detail == {"headline": ["Locked."]}


@pytest.mark.django_db
class TestTheBulkPath:
    def test_a_many_dispatch_renames_through_the_child(self) -> None:
        spec = ServiceSpec(
            service=_refuse([{}, {"title": ["Too long."]}]),
            input_serializer=_PostInput,
            many=True,
        )

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=[_PAYLOAD, _PAYLOAD])

        assert excinfo.value.detail == [{}, {"headline": ["Too long."]}]


@pytest.mark.django_db
def test_an_author_can_still_reach_the_model_name() -> None:
    """The escape hatch: a serializer that does not alias renames nothing."""

    class _Plain(serializers.Serializer):
        title = serializers.CharField()

    spec = ServiceSpec(service=_refuse({"title": ["Too long."]}), input_serializer=_Plain)

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"title": "t"})

    assert excinfo.value.detail == {"title": ["Too long."]}


@pytest.mark.django_db
def test_a_model_serializer_needs_no_declaration() -> None:
    """The common case: ModelSerializer fields are named after their columns."""

    class _PostModelInput(serializers.ModelSerializer):
        class Meta:
            model = Post
            fields = ("title",)

    spec = ServiceSpec(service=_refuse({"title": ["Too long."]}), input_serializer=_PostModelInput)

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"title": "t"})

    assert excinfo.value.detail == {"title": ["Too long."]}
