"""A ``LIST`` output declaration with no re-read presents the service's return as a list.

``output_selector_spec=SelectorSpec(kind=LIST)`` with no ``selector`` re-reads
nothing, so what dispatch presents is the service's own return, and the
declaration says that return is a set: dispatch reports ``kind="list"``, the
output schema is an array, and ``can_present_nothing`` answers ``False``. A return
that is not a set of rows is the author's error, raised after the write.

Each case runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core reports the kind on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    can_present_nothing,
    dispatch_spec,
    render_spec_output,
    spec_to_json_schema,
)
from rest_framework_services.jsonschema.spec_to_json_schema import _rendered_kind
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, params={}, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, params={}, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _PostOut(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


_DECLARED_LIST = SelectorSpec(kind=SelectorKind.LIST, output_serializer=_PostOut)


def _spec(service: Callable[..., Any], *, allow_none: bool = False) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=service, output_selector_spec=_DECLARED_LIST, allow_none=allow_none, atomic=False
    )


def _publish_as_queryset() -> QuerySet[Post]:
    Post.objects.create(title="b")
    return Post.objects.order_by("title")


def _publish_as_list() -> list[Post]:
    Post.objects.create(title="b")
    return list(Post.objects.order_by("title"))


def _publish_as_generator() -> Iterator[Post]:
    Post.objects.create(title="b")
    return (post for post in list(Post.objects.order_by("title")))


_RETURNS = pytest.mark.parametrize(
    "service",
    [_publish_as_queryset, _publish_as_list, _publish_as_generator],
    ids=["queryset", "list", "generator"],
)


@pytest.mark.django_db
@_CORES
@_RETURNS
def test_the_services_own_return_is_presented_as_a_list(
    dispatch: Dispatch, service: Callable[..., Any]
) -> None:
    Post.objects.create(title="a")
    spec = _spec(service)

    result = dispatch(spec)

    assert result.kind == "list"
    payload = render_spec_output(spec, result.value, many=True)
    assert [row["title"] for row in payload] == ["a", "b"]
    schema = spec_to_json_schema(spec, phase="output")
    assert schema is not None
    assert not list(Draft202012Validator(schema).iter_errors(payload))


@pytest.mark.django_db
@_CORES
def test_the_return_is_passed_through_as_it_came(dispatch: Dispatch) -> None:
    # docs/reference/dispatch.md: a queryset stays lazy, and anything else is the
    # object the service returned.
    rows = [Post.objects.create(title="a")]

    assert dispatch(_spec(lambda: rows)).value is rows
    queryset = dispatch(_spec(lambda: Post.objects.all())).value
    assert isinstance(queryset, QuerySet)
    assert queryset._result_cache is None


@pytest.mark.django_db
@_CORES
def test_an_empty_return_is_an_empty_list_rather_than_nothing(dispatch: Dispatch) -> None:
    result = dispatch(_spec(lambda: []))

    assert result.kind == "list"
    assert result.value == []


def _writes_then_returns(value: Any) -> Callable[[], Any]:
    def service() -> Any:
        Post.objects.create(title="written")
        return value

    return service


_REFUSED = pytest.mark.parametrize(
    ("returned", "type_name"),
    [
        pytest.param({"id": 1}, "dict", id="mapping"),
        pytest.param("ab", "str", id="str"),
        pytest.param(b"ab", "bytes", id="bytes"),
        pytest.param(3, "int", id="non-iterable"),
        pytest.param(None, "NoneType", id="none"),
    ],
)


@pytest.mark.django_db
@_CORES
@_REFUSED
def test_a_return_that_is_not_a_set_of_rows_is_the_authors_error(
    dispatch: Dispatch, returned: Any, type_name: str
) -> None:
    """Raised loudly, naming the declaration and what came back, and not as a
    refusal: the write has already happened, so a caller must not read it as
    nothing having changed."""
    # ``atomic`` as declared by default: the service's own block has closed by the
    # time its return is read, so the row it wrote is still there afterwards.
    spec = ServiceSpec(service=_writes_then_returns(returned), output_selector_spec=_DECLARED_LIST)
    with pytest.raises(ImproperlyConfigured) as excinfo:
        dispatch(spec)

    message = str(excinfo.value)
    assert "output_selector_spec declares kind=LIST with no selector" in message
    assert f"returned {type_name}" in message
    assert Post.objects.filter(title="written").count() == 1


@pytest.mark.django_db
@_CORES
def test_allow_none_does_not_let_a_list_declaration_present_nothing(dispatch: Dispatch) -> None:
    # A list is never ``None``, only empty, so ``allow_none`` is not read here.
    with pytest.raises(ImproperlyConfigured, match="returned NoneType"):
        dispatch(_spec(_writes_then_returns(None), allow_none=True))


@pytest.mark.parametrize("allow_none", [False, True])
def test_the_schema_the_kind_and_the_predicate_agree(allow_none: bool) -> None:
    spec = _spec(_publish_as_list, allow_none=allow_none)

    assert _rendered_kind(spec, _DECLARED_LIST) is SelectorKind.LIST
    assert can_present_nothing(spec) is False
    schema = spec_to_json_schema(spec, phase="output")
    assert schema is not None
    assert schema["type"] == "array"
    # One row per item, and never ``["object", "null"]``, whatever ``allow_none`` says.
    assert schema["items"]["type"] == "object"
    assert set(schema["items"]["properties"]) == {"id", "title"}


@pytest.mark.django_db
@_CORES
def test_a_retrieve_declaration_with_no_re_read_is_still_one_value(dispatch: Dispatch) -> None:
    # The other arm of the same declaration: the service's return is presented as
    # the one value it is, whatever it is, and nothing is refused.
    nested = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_PostOut)
    spec = ServiceSpec(service=lambda: {"id": 1}, output_selector_spec=nested, atomic=False)

    result = dispatch(spec)

    assert result.kind == "instance"
    assert result.value == {"id": 1}
    assert _rendered_kind(spec, nested) is SelectorKind.RETRIEVE
    schema = spec_to_json_schema(spec, phase="output")
    assert schema is not None
    assert schema["type"] == "object"
