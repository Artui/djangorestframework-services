"""A nested selector's ``progress_reporter`` is refused at ``as_view()``.

Only the spec being dispatched runs, so only its ``progress_reporter`` reports.
One declared on a nested selector would answer every request with an error, so
it fails where a nested ``preconditions`` does: when the view is built.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
)
from rest_framework_services.views.spec_validation import validate_service_spec


class _In(serializers.Serializer):
    title = serializers.CharField()


def _progress() -> None:
    return None


def _nested(kind: SelectorKind = SelectorKind.RETRIEVE) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=kind, selector=lambda: None, progress_reporter=_progress)


def _service(**nested: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=lambda **_: None, input_serializer=_In, permission_classes=[], **nested
    )


def test_an_output_selector_s_is_refused_by_a_create_view() -> None:
    spec = _service(output_selector_spec=_nested())

    with pytest.raises(ImproperlyConfigured, match="progress_reporter"):
        type("_View", (ServiceCreateView,), {"spec": spec}).as_view()


def test_an_instance_selector_s_is_refused_by_an_update_view() -> None:
    spec = _service(instance_selector_spec=_nested())

    with pytest.raises(ImproperlyConfigured, match="progress_reporter"):
        type("_View", (ServiceUpdateView,), {"spec": spec}).as_view()


def test_a_collection_selector_s_is_refused() -> None:
    spec = _service(collection_selector_spec=_nested(SelectorKind.LIST))

    with pytest.raises(ImproperlyConfigured, match="progress_reporter"):
        validate_service_spec(spec, label="X", has_instance=False, permissive_extras=False)
