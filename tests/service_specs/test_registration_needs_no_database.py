"""Registering a spec never needs the database, and what it cannot check there is checked at dispatch.

A spec declaring a read describes its input serializer as it compiles, to
refuse a read named like a writable field. A spec registered at import,
before ``migrate`` has made a table, may have a serializer whose fields query
one, or look up a row that is not there yet; it is taken to declare no name,
as a serializer reading a request no import has is, and registers. The
refusal it could not make then is made when the call describes it.

A ``many`` spec reads its list bounds off the list serializer DRF builds for
its input, which builds the row serializer too. One that cannot be built with
no call registers with no bound of its own, and the list serializer that
validates the call still holds the list to the bound it declares.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    QueryParam,
    ServiceSpec,
    SpecRegistry,
    dispatch_spec,
)
from tests.testapp.models import Tag

FAILING: dict[str, str | None] = {"how": None}


def _missing_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM a_table_migrate_has_not_made")


_FAILURES: dict[str, Any] = {
    # A table ``migrate`` has not made yet: ``OperationalError``, a ``DatabaseError``.
    "missing-table": _missing_table,
    # A row no fixture has loaded yet: the model's ``DoesNotExist``.
    "missing-row": lambda: Tag.objects.get(name="absent"),
    # The request a compile has not got, read three ways.
    "attribute": lambda context: context["request"].user,
    "key": lambda context: context["absent"],
    "subscript": lambda context: context["request"]["user"],
}


class _FieldsFromTheDatabase(serializers.Serializer):
    title = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        how = FAILING["how"]
        if how in ("missing-table", "missing-row"):
            _FAILURES[how]()
        elif how is not None:
            _FAILURES[how](self.context)
        return fields


class _BuiltFromTheDatabase(serializers.Serializer):
    title = serializers.CharField()

    @classmethod
    def many_init(cls, *args: Any, **kwargs: Any) -> serializers.ListSerializer:
        return super().many_init(*args, max_length=2, **kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        how = FAILING["how"]
        if how in ("missing-table", "missing-row"):
            _FAILURES[how]()
        elif how is not None:
            _FAILURES[how](self.context)


def _service(*, data: Any) -> Any:
    return dict(data)


def _rows(*, data: Any) -> Any:
    return list(data)


def _many_spec() -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_rows,
        input_serializer=_BuiltFromTheDatabase,
        many=True,
        permission_classes=[],
        atomic=False,
    )


def _spec() -> ServiceSpec[Any, Any, Any]:
    # A read named like the writable field ``title``: refused where the
    # serializer's fields can be read.
    return ServiceSpec(
        service=_service,
        input_serializer=_FieldsFromTheDatabase,
        reads=(QueryParam("title"),),
        permission_classes=[],
        atomic=False,
    )


@pytest.fixture(autouse=True)
def _fields_read() -> Any:
    yield
    FAILING["how"] = None


@pytest.mark.django_db
@pytest.mark.parametrize("how", list(_FAILURES))
def test_a_spec_whose_fields_cannot_be_read_without_a_call_registers(how: str) -> None:
    FAILING["how"] = how

    SpecRegistry().register("titled", _spec())


@pytest.mark.django_db
def test_with_its_fields_read_the_read_is_refused_at_registration() -> None:
    with pytest.raises(ImproperlyConfigured, match="also takes as a writable field"):
        SpecRegistry().register("titled", _spec())


@pytest.mark.django_db
def test_registered_before_the_table_exists_it_is_refused_at_dispatch() -> None:
    spec = _spec()
    FAILING["how"] = "missing-table"
    SpecRegistry().register("titled", spec)

    FAILING["how"] = None
    # The kernel's own refusal of a name two sources declare answers first.
    with pytest.raises(ImproperlyConfigured, match=r"\['title'\] are declared by two sources"):
        dispatch_spec(spec, user=None, params={"title": "a"})


@pytest.mark.django_db
@pytest.mark.parametrize("how", list(_FAILURES))
def test_a_many_spec_whose_serializer_cannot_be_built_without_a_call_registers(how: str) -> None:
    FAILING["how"] = how

    SpecRegistry().register("rows", _many_spec())


@pytest.mark.django_db
def test_registered_before_the_table_exists_its_list_is_still_bounded() -> None:
    spec = _many_spec()
    FAILING["how"] = "missing-table"
    SpecRegistry().register("rows", spec)

    FAILING["how"] = None
    rows = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    with pytest.raises(ValidationError, match="no more than 2"):
        dispatch_spec(spec, user=None, params=rows)
