"""A ``LIST`` output declaration with no re-read presents the service's return as a list.

``output_selector_spec=SelectorSpec(kind=LIST)`` with no ``selector`` re-reads
nothing, so what dispatch presents is the service's own return, and the
declaration says that return is a set: dispatch reports ``kind="list"``, the
output schema is an array, and ``can_present_nothing`` answers ``False``. A return
that is not a set of rows is the author's error, raised after the write.

Each case runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core reports the kind on its own. The cases reading the return also run with the
kernel answering ``kind="list"`` itself, as it does for a spec compiled with
``returns_many``: the edge decides by the declaration, so a return that is not a
set of rows is refused whichever kind the kernel answered.
"""

from __future__ import annotations

import dataclasses
import importlib
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


def _as_returns_many(result: Any) -> Any:
    # The kernel's own answer for a ``returns_many`` spec: ``"list"`` for a
    # return that is not ``None``, ``"instance"`` for ``None``.
    if result.kind == "instance" and result.value is not None:
        return dataclasses.replace(result, kind="list")
    return result


@pytest.fixture(params=["as-compiled", "kernel-answers-list"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Which kind the kernel answers a declared list's return with.

    ``kernel-answers-list`` wraps the name each core calls the kernel by, so the
    edge meets the result a ``returns_many`` compile produces whatever the
    compiled spec says today.
    """
    if request.param == "kernel-answers-list":
        sync_core = importlib.import_module("rest_framework_services.dispatch.dispatch_spec")
        async_core = importlib.import_module("rest_framework_services.dispatch.adispatch_spec")
        kernel_dispatch = sync_core.dispatch
        kernel_adispatch = async_core.adispatch

        def listing(*args: Any, **kwargs: Any) -> Any:
            return _as_returns_many(kernel_dispatch(*args, **kwargs))

        async def alisting(*args: Any, **kwargs: Any) -> Any:
            return _as_returns_many(await kernel_adispatch(*args, **kwargs))

        monkeypatch.setattr(sync_core, "dispatch", listing)
        monkeypatch.setattr(async_core, "adispatch", alisting)


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
@pytest.mark.usefixtures("kernel")
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
@pytest.mark.usefixtures("kernel")
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
@pytest.mark.usefixtures("kernel")
def test_a_return_that_is_not_a_set_of_rows_is_the_authors_error(
    dispatch: Dispatch, returned: Any, type_name: str
) -> None:
    """Raised loudly, naming the declaration and what came back, and not as a
    refusal: the write has already happened, so a caller must not read it as
    nothing having changed. Under ``kernel-answers-list`` the mapping, ``str``
    and ``bytes`` rows come back from the kernel as ``"list"``, so deciding by
    the result's kind rather than the declaration presents them row by row."""
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


@pytest.mark.django_db
@_CORES
def test_a_missing_row_under_a_list_declaration_stays_not_found(dispatch: Dispatch) -> None:
    lookup = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda *, pk: Post.objects.filter(pk=pk)
    )
    spec = ServiceSpec(
        service=lambda *, instance: [instance],
        instance_selector_spec=lookup,
        output_selector_spec=_DECLARED_LIST,
        atomic=False,
    )

    result = dispatch(spec, instance=None)

    assert result.kind == "not_found"
    assert result.value is None


@pytest.mark.django_db
@_CORES
def test_a_list_output_that_re_reads_is_the_output_selector_s(dispatch: Dispatch) -> None:
    # What the output selector answers is the kernel's re-read, never the
    # service's return, so it is not held to the service's list: deciding the
    # declaration without its ``selector`` would refuse this ``None`` blaming the
    # service.
    re_read = SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda: None, output_serializer=_PostOut
    )
    spec = ServiceSpec(service=lambda: [], output_selector_spec=re_read, atomic=False)

    result = dispatch(spec)

    assert result.kind == "list"
    assert result.value is None
