"""A service whose ``LIST`` output re-reads nothing presents its own return as rows.

drfs declares that by an ``output_selector_spec`` of ``kind=LIST`` with no
``selector``: the output serializer presents each row the service returns. The
kernel's spelling of the same is ``returns_many=True`` beside no output
selector, so its own ``dispatch`` answers a list, and its output schema is an
array of the item. ``allow_none`` stays off, as the edge refuses a ``None``
return as the author's error.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.present import present
from django_service_specs.schema.spec_output_schema import spec_output_schema
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

pytestmark = pytest.mark.django_db


class _Row(serializers.Serializer):
    title = serializers.CharField()


class _Rows(serializers.Serializer):
    titles = serializers.ListField(child=serializers.CharField())


def _rows() -> list[dict[str, str]]:
    return [{"title": "first"}, {"title": "second"}]


def _rerun(*, result: Any) -> Any:
    return result


def _listed(**overrides: Any) -> ServiceSpec[Any, Any, Any]:
    fields: dict[str, Any] = {
        "service": _rows,
        "permission_classes": [AllowAny],
        "output_selector_spec": SelectorSpec(
            kind=SelectorKind.LIST, selector=None, output_serializer=_Row
        ),
    }
    fields.update(overrides)
    return ServiceSpec(**fields)


def _compiled(spec: ServiceSpec[Any, Any, Any]) -> KernelServiceSpec:
    compiled = kernel_spec(spec)
    assert isinstance(compiled, KernelServiceSpec)
    return compiled


def test_a_list_output_with_no_re_read_compiles_to_returns_many() -> None:
    compiled = _compiled(_listed(allow_none=True))

    assert compiled.returns_many is True
    assert compiled.output_selector_spec is None
    assert compiled.allow_none is False


def test_the_kernel_s_own_dispatch_answers_the_rows() -> None:
    spec = _listed()

    result = dispatch(spec, principal=AnonymousUser(), arguments={})

    assert result.kind == "list"
    assert present(spec, result) == [{"title": "first"}, {"title": "second"}]


def test_the_kernel_s_output_schema_is_an_array_of_the_row() -> None:
    schema = spec_output_schema(_compiled(_listed()))

    assert schema["type"] == "array"
    assert schema["items"]["properties"] == {"title": {"type": "string"}}


@pytest.mark.parametrize(
    "spec",
    [
        # Each case holds one condition of the declaration ``_service`` reads,
        # and fails without it.
        pytest.param(
            _listed(
                many=True,
                input_serializer=_Row,
                service=lambda *, data: data,
            ),
            # The kernel refuses ``returns_many`` beside ``many``, which lists
            # the return already.
            id="many",
        ),
        pytest.param(
            _listed(
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, selector=None, output_serializer=_Rows
                )
            ),
            id="retrieve-output",
        ),
        pytest.param(
            _listed(
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_rerun, output_serializer=_Row
                )
            ),
            # The kernel refuses ``returns_many`` beside an output selector.
            id="list-re-read",
        ),
        pytest.param(_listed(output_selector_spec=None), id="no-output"),
    ],
)
def test_only_a_list_output_with_no_re_read_and_no_many_returns_many(
    spec: ServiceSpec[Any, Any, Any],
) -> None:
    assert _compiled(spec).returns_many is False
