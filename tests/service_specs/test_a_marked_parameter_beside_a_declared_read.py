"""A parameter the callable marks ``InputRequired``, or a required ``TypedDict`` key, under a plain declared read.

The read says where the value comes from, not that the callable can run
without one, so the parameter stays required on the compiled parameters and the
kernel's input schema, as it is with no read declared. Three consequences:

- off a handed row, the parameter is refused when left out, even with a
  ``None`` default (the marker is what the callable declared);
- a required key of an ``Unpack``-ed ``TypedDict`` left out is refused as
  ``InvalidArguments`` rather than raising ``KeyError`` in the callable;
- the DRF edge, which never runs a target lookup for a row a view hands in,
  still answers without the lookup's key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.schema.spec_input_schema import spec_input_schema
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict, Unpack

from rest_framework_services import adispatch_spec, dispatch_spec
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.views.query.selector_list_view import SelectorListView
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

pytestmark = pytest.mark.django_db


class _Pk(TypedDict):
    pk: int


class _PkMarked(TypedDict):
    pk: Annotated[int, InputRequired]


def _marked(*, pk: Annotated[int, InputRequired]) -> Any:
    return Post.objects.get(pk=pk)


def _marked_none(*, pk: Annotated[int | None, InputRequired] = None) -> Any:
    return Post.objects.get(pk=pk)


def _keyed(**kwargs: Unpack[_Pk]) -> Any:
    return Post.objects.get(pk=kwargs["pk"])


def _keyed_marked(**kwargs: Unpack[_PkMarked]) -> Any:
    return Post.objects.get(pk=kwargs["pk"])


def _untouched(*, instance: Any) -> Any:
    return instance.title


_LOOKUPS = pytest.mark.parametrize(
    "lookup",
    [_marked, _marked_none, _keyed, _keyed_marked],
    ids=["marked", "marked-none-default", "typed-dict-key", "typed-dict-marked-key"],
)
_READS = pytest.mark.parametrize(
    "declare", [QueryParam, UrlKwarg], ids=["query-param", "url-kwarg"]
)


def _service(lookup: Callable[..., Any], read: Any, on: str) -> Any:
    return ServiceSpec(
        service=_untouched,
        permission_classes=[AllowAny],
        reads=[read] if on == "spec" else [],
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lookup, reads=[read] if on == "lookup" else []
        ),
    )


@pytest.mark.parametrize(
    "edge", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
@pytest.mark.parametrize("on", ["spec", "lookup"])
@_READS
@_LOOKUPS
def test_a_handed_row_is_answered_without_a_key_the_lookup_marks_and_the_spec_declares(
    edge: Any, lookup: Callable[..., Any], declare: Any, on: str
) -> None:
    """The lookup never runs for a handed row, so nobody needs its key.

    Fails without the declared names in the lookup branch of
    ``required_for_having_no_default``, which relaxes a marked or ``TypedDict``
    key the spec declares a read for.
    """
    post = Post.objects.create(title="handed")

    result = edge(_service(lookup, declare("pk"), on), user=None, params={}, instance=post)

    assert result.value == "handed"


@_READS
@_LOOKUPS
def test_the_kernel_still_requires_the_key_the_lookup_marks_under_a_read(
    lookup: Callable[..., Any], declare: Any
) -> None:
    spec = _service(lookup, declare("pk"), "spec")
    post = Post.objects.create(title="handed")

    compiled = kernel_spec(spec)
    assert compiled.target_selector_spec().parameters().get("pk").required is True
    assert "pk" in spec_input_schema(compiled)["required"]
    with pytest.raises(KernelInvalidArguments) as refused:
        dispatch(spec, principal=AnonymousUser(), arguments={}, target=post)
    assert set(refused.value.detail) == {"pk"}


def _selector_spec(selector: Callable[..., Any], read: Any) -> Any:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        reads=[read],
        permission_classes=[AllowAny],
        output_serializer=PostSerializer,
    )


def _ticket_marked_none(*, ticket: Annotated[str | None, InputRequired] = None) -> list[Any]:
    return []


class _Ticket(TypedDict):
    ticket: str


class _TicketMarked(TypedDict):
    ticket: Annotated[str, InputRequired]


def _ticket_keyed(**kwargs: Unpack[_Ticket]) -> list[Any]:
    return [kwargs["ticket"]]


def _ticket_keyed_marked(**kwargs: Unpack[_TicketMarked]) -> list[Any]:
    return [kwargs["ticket"]]


_NO_VALUE = pytest.mark.parametrize(
    "selector",
    [_ticket_marked_none, _ticket_keyed, _ticket_keyed_marked],
    ids=["marked-none-default", "typed-dict-key", "typed-dict-marked-key"],
)


@_NO_VALUE
def test_a_marked_parameter_stays_required_under_a_declared_read(
    selector: Callable[..., Any],
) -> None:
    """The marker wins over the read, even with a ``None`` default; a ``TypedDict`` key is refused, not a ``KeyError``."""
    spec = _selector_spec(selector, QueryParam("ticket"))

    compiled = kernel_spec(spec)
    assert compiled.parameters().get("ticket").required is True
    assert "ticket" in spec_input_schema(compiled)["required"]
    with pytest.raises(InvalidArguments) as refused:
        dispatch_spec(spec, user=None, params={})
    assert set(refused.value.detail) == {"ticket"}


@_NO_VALUE
def test_a_live_view_refuses_a_missing_marked_parameter_under_a_declared_read(
    selector: Callable[..., Any],
) -> None:
    class _View(SelectorListView):
        spec = _selector_spec(selector, QueryParam("ticket"))

    response = _View.as_view()(APIRequestFactory().get("/"))

    assert response.status_code == 400
    assert set(response.data) == {"ticket"}
