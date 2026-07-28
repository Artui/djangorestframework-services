"""Unit tests for ``validate_channel_names``."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.types.validate_channel_names import validate_channel_names


def test_accepts_a_clean_declaration_set() -> None:
    validate_channel_names(
        label="tool 'list_widgets'",
        kind="url_kwargs",
        declarations=[UrlKwarg("project_pk", type="integer"), UrlKwarg("team_pk")],
    )


def test_accepts_an_empty_declaration_set() -> None:
    validate_channel_names(label="tool 'x'", kind="url_kwargs", declarations=[])


def test_rejects_a_reserved_pool_seed_without_the_caller_naming_it() -> None:
    # ``RESERVED_POOL_SEEDS`` is always included — this is the drift the shared
    # validator exists to prevent, so it must not depend on ``reserved``.
    with pytest.raises(ImproperlyConfigured, match=r"url_kwargs name\(s\) \['user'\]"):
        validate_channel_names(label="tool 'x'", kind="url_kwargs", declarations=[UrlKwarg("user")])


def test_rejects_a_transport_reserved_name() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"query_params name\(s\) \['page'\]"):
        validate_channel_names(
            label="tool 'x'",
            kind="query_params",
            declarations=[QueryParam("page")],
            reserved=frozenset({"page", "limit", "order"}),
        )


def test_rejects_duplicate_names() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"duplicate url_kwargs name\(s\) \['pk'\]"):
        validate_channel_names(
            label="tool 'x'",
            kind="url_kwargs",
            declarations=[UrlKwarg("pk"), UrlKwarg("pk", type="integer")],
        )


def test_rejects_required_together_with_a_default() -> None:
    with pytest.raises(ImproperlyConfigured, match="cannot also be required"):
        validate_channel_names(
            label="tool 'x'",
            kind="url_kwargs",
            declarations=[UrlKwarg("pk", default=1, required=True)],
        )


def test_allows_required_without_a_default() -> None:
    validate_channel_names(
        label="tool 'x'", kind="url_kwargs", declarations=[UrlKwarg("pk", required=True)]
    )


def test_allows_a_default_without_required() -> None:
    validate_channel_names(
        label="tool 'x'", kind="url_kwargs", declarations=[UrlKwarg("pk", default=1)]
    )


def test_query_params_carry_no_required_attribute() -> None:
    # ``QueryParam`` deliberately has no ``required``; the contradiction check
    # must tolerate that rather than assume the attribute exists.
    validate_channel_names(
        label="tool 'x'", kind="query_params", declarations=[QueryParam("fields", default="id")]
    )
