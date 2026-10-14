"""Tests for the helpers in selectors/utils.py, exported and not."""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs.selectors.amaterialize_retrieve import (
    amaterialize_retrieve as kernel_amaterialize_retrieve,
)
from django_service_specs.selectors.arun_selector import arun_selector as kernel_arun_selector
from django_service_specs.selectors.is_queryset import is_queryset as kernel_is_queryset
from django_service_specs.selectors.materialize_retrieve import (
    materialize_retrieve as kernel_materialize_retrieve,
)
from django_service_specs.selectors.run_selector import run_selector as kernel_run_selector

import rest_framework_services
from rest_framework_services.selectors import utils


@pytest.mark.parametrize(
    ("name", "kernel_object"),
    [
        ("run_selector", kernel_run_selector),
        ("arun_selector", kernel_arun_selector),
        ("is_queryset", kernel_is_queryset),
        ("materialize_retrieve", kernel_materialize_retrieve),
        ("amaterialize_retrieve", kernel_amaterialize_retrieve),
    ],
)
def test_the_selector_runners_are_the_kernel_s(name: str, kernel_object: Any) -> None:
    # The kernel owns their behaviour and tests it; drfs keeps this module's path
    # and the root name.
    assert getattr(utils, name) is kernel_object
    assert getattr(rest_framework_services, name) is kernel_object


class TestFilterSetAcceptsRequest:
    def test_true_when_request_declared(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any, request: Any = None) -> None: ...

        assert utils._filter_set_accepts_request(_FS) is True

    def test_true_when_var_keyword(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any, **kwargs: Any) -> None: ...

        assert utils._filter_set_accepts_request(_FS) is True

    def test_false_for_bare_data_queryset(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any) -> None: ...

        assert utils._filter_set_accepts_request(_FS) is False
