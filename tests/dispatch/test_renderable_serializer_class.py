"""``renderable_serializer_class`` — what an output declaration renders through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services import renderable_serializer_class


@dataclass
class _Word:
    word: str


class _WordSerializer(serializers.Serializer):
    word = serializers.CharField()


class _ReadOnlyWord(serializers.BaseSerializer):
    def to_representation(self, instance: Any) -> Any:
        return instance.word


class _NotADataclass:
    word: str = "plain"


class TestResolution:
    def test_none_stays_none(self) -> None:
        assert renderable_serializer_class(None) is None

    def test_a_serializer_subclass_is_returned_as_declared(self) -> None:
        assert renderable_serializer_class(_WordSerializer) is _WordSerializer

    def test_a_base_serializer_subclass_is_returned_as_declared(self) -> None:
        assert renderable_serializer_class(_ReadOnlyWord) is _ReadOnlyWord

    def test_a_dataclass_type_is_wrapped(self) -> None:
        cls = renderable_serializer_class(_Word)
        assert issubclass(cls, DataclassSerializer)
        assert cls.Meta.dataclass is _Word  # ty: ignore[unresolved-attribute]

    def test_the_wrapper_renders_single_and_many(self) -> None:
        cls = renderable_serializer_class(_Word)
        assert cls(_Word(word="hi")).data == {"word": "hi"}
        assert cls([_Word(word="a"), _Word(word="b")], many=True).data == [
            {"word": "a"},
            {"word": "b"},
        ]

    def test_a_dataclass_instance_is_not_wrapped(self) -> None:
        """Holds the ``isinstance(declared, type)`` conjunct: ``is_dataclass`` alone
        is true of an instance too, which has no ``__name__`` to build a class from."""
        instance = _Word(word="hi")
        assert renderable_serializer_class(instance) is instance  # ty: ignore[no-matching-overload]

    def test_a_class_that_is_not_a_dataclass_is_not_wrapped(self) -> None:
        """Holds the ``is_dataclass`` conjunct."""
        assert renderable_serializer_class(_NotADataclass) is _NotADataclass


class TestIdentity:
    def test_one_class_per_dataclass(self) -> None:
        assert renderable_serializer_class(_Word) is renderable_serializer_class(_Word)

    def test_distinct_dataclasses_get_distinct_classes(self) -> None:
        @dataclass
        class _Other:
            word: str

        other = renderable_serializer_class(_Other)
        assert other is not renderable_serializer_class(_Word)
        assert other.Meta.dataclass is _Other  # ty: ignore[unresolved-attribute]
