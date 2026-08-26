"""Unit tests for ``UrlKwarg`` and ``QueryParam`` schema fragments."""

from __future__ import annotations

from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.url_kwarg import UrlKwarg


def test_url_kwarg_defaults_to_an_optional_string() -> None:
    kwarg = UrlKwarg("project_pk")
    assert kwarg.json_schema() == {"type": "string"}
    assert kwarg.required is False


def test_url_kwarg_schema_carries_type_description_and_default() -> None:
    kwarg = UrlKwarg("project_pk", type="integer", description="Owning project.", default=1)
    assert kwarg.json_schema() == {
        "type": "integer",
        "description": "Owning project.",
        "default": 1,
    }


def test_url_kwarg_required_is_not_a_schema_property() -> None:
    # ``required`` is a sibling list on the *object* schema, never a property
    # keyword — the adapter merges the name into it.
    assert UrlKwarg("pk", required=True).json_schema() == {"type": "string"}


def test_url_kwarg_is_hashable_and_frozen() -> None:
    assert UrlKwarg("pk") == UrlKwarg("pk")
    assert len({UrlKwarg("pk"), UrlKwarg("pk")}) == 1


def test_query_param_schema_carries_type_description_and_default() -> None:
    param = QueryParam("fields", type="array", description="Sparse fieldset.", default="id")
    assert param.json_schema() == {
        "type": "array",
        "description": "Sparse fieldset.",
        "default": "id",
    }


def test_query_param_defaults_to_an_optional_string() -> None:
    assert QueryParam("fields").json_schema() == {"type": "string"}


def test_url_kwarg_without_a_default_declares_none() -> None:
    # ``UNSET``, not ``None`` — otherwise "no default" and "defaults to null"
    # are the same declaration and the schema cannot tell them apart.
    assert UrlKwarg("project_pk").default is UNSET


def test_url_kwarg_surfaces_an_explicit_null_default() -> None:
    assert UrlKwarg("parent_id", type="integer", default=None).json_schema() == {
        "type": "integer",
        "default": None,
    }


def test_query_param_without_a_default_declares_none() -> None:
    assert QueryParam("fields").default is UNSET


def test_query_param_surfaces_an_explicit_null_default() -> None:
    assert QueryParam("parent_id", type="integer", default=None).json_schema() == {
        "type": "integer",
        "default": None,
    }
