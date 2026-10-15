"""A declared read named like a writable input field is refused when the spec compiles.

The body's value of that name is the field's, so a ``QueryParam`` of the name
would reach nothing: not the service, whose parameter the field fills, and not
a target lookup reading the name, which takes it from the route alone. A
``UrlKwarg`` of the name is the rename-by-key shape where the target lookup reads
it, and is refused elsewhere, where the kernel would otherwise refuse it at
dispatch as declared by two sources.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services import (
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UrlKwarg,
)
from rest_framework_services.registry.spec_registry import SpecRegistry
from tests.testapp.models import Post


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


class _ReadOnlyTitleIn(serializers.Serializer):
    title = serializers.CharField(read_only=True)
    body = serializers.CharField()


def _reading(*, pk: int, title: str = "D-lookup") -> Any:
    return Post.objects.filter(pk=pk)


def _not_reading(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _service(*, data: Any, instance: Any = None, title: str = "D-svc") -> Any:
    return instance


def _spec(
    *,
    reads: Any = (),
    lookup: Any = None,
    lookup_reads: Any = (),
    serializer: Any = _TitleIn,
    many: bool = False,
) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_service,
        input_serializer=serializer,
        reads=reads,
        instance_selector_spec=(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lookup, reads=lookup_reads)
            if lookup is not None
            else None
        ),
        many=many,
        permission_classes=[],
        atomic=False,
    )


def _collection(*, title: str = "D-lookup") -> Any:
    return Post.objects.filter(title=title)


def _over_a_collection(reads: Any) -> ServiceSpec[Any, Any, Any]:
    def service(*, data: Any, collection: Any, title: str = "D-svc") -> Any:
        return collection

    return ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=_collection, reads=reads
        ),
        permission_classes=[],
        atomic=False,
    )


def _register(spec: ServiceSpec[Any, Any, Any]) -> None:
    SpecRegistry().register("edit", spec)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        pytest.param(
            _spec(reads=(QueryParam("title"),), lookup=_reading),
            r"ServiceSpec\.reads declares QueryParam\('title'\), which _TitleIn also takes "
            r"as a writable field and the target lookup reads\.",
            id="query-param-the-lookup-reads",
        ),
        pytest.param(
            _spec(reads=(QueryParam("title"),)),
            r"ServiceSpec\.reads declares QueryParam\('title'\), which _TitleIn also takes "
            r"as a writable field\.",
            id="query-param",
        ),
        pytest.param(
            _spec(reads=(UrlKwarg("title"),), lookup=_not_reading),
            r"ServiceSpec\.reads declares UrlKwarg\('title'\), which _TitleIn also takes "
            r"as a writable field\.",
            id="url-kwarg-no-lookup-reads",
        ),
        pytest.param(
            _spec(lookup=_reading, lookup_reads=(QueryParam("title"),)),
            r"ServiceSpec\.instance_selector_spec\.reads declares QueryParam\('title'\), "
            r"which _TitleIn also takes as a writable field and the target lookup reads\.",
            id="the-lookups-query-param",
        ),
        pytest.param(
            _over_a_collection((QueryParam("title"),)),
            r"ServiceSpec\.collection_selector_spec\.reads declares QueryParam\('title'\), "
            r"which _TitleIn also takes as a writable field and the target lookup reads\.",
            id="the-collection-lookups-query-param",
        ),
    ],
)
def test_a_read_named_like_a_writable_field_is_refused(
    spec: ServiceSpec[Any, Any, Any], message: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=message) as excinfo:
        _register(spec)

    # The kernel's own refusal, which would answer at dispatch, is not this one.
    assert "declared by two sources" not in str(excinfo.value)


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            _spec(reads=(UrlKwarg("title"),), lookup=_reading), id="url-kwarg-the-lookup-reads"
        ),
        pytest.param(
            _spec(lookup=_reading, lookup_reads=(UrlKwarg("title"),)),
            id="the-lookups-url-kwarg",
        ),
        pytest.param(
            _spec(reads=(QueryParam("title"),), serializer=_ReadOnlyTitleIn), id="read-only-field"
        ),
        pytest.param(_spec(reads=(QueryParam("title"),), many=True), id="a-rows-field"),
        pytest.param(
            _over_a_collection((UrlKwarg("title"),)), id="the-collection-lookups-url-kwarg"
        ),
    ],
)
def test_a_read_no_field_takes_from_the_body_is_accepted(
    spec: ServiceSpec[Any, Any, Any],
) -> None:
    _register(spec)


class _RequestReadingIn(serializers.Serializer):
    """A serializer whose fields read the request, which a description has not got."""

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["title"] = serializers.CharField(required=self.context["request"].user.is_staff)
        return fields


def test_a_spec_declaring_no_read_registers_without_describing_its_serializer() -> None:
    _register(_spec(serializer=_RequestReadingIn))
