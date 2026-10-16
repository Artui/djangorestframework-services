"""When ``REJECT`` raises on a ``**kwargs: Unpack[...]`` it cannot resolve.

``docs/recipes/off-http-inputs.md`` states the matrix, so each row is asserted
here, off HTTP and over a live view alike. ``REJECT`` raises
``ImproperlyConfigured`` (``UnknownArguments.REJECT cannot be enforced``) only
where the unresolvable annotation is the basis for refusing a key:

- a selector's, top-level or a target lookup's, under every binding;
- a serializer-less service's, under ``SPREAD_AUTHOR_WINS`` alone, where its
  parameters are the caller's input.

It does not raise for a service's under ``AUTO`` or ``BUNDLE`` (its signature is
not read), for a service with an input serializer (the serializer declares the
input), or beside a target lookup whose selector takes a bare ``**kwargs``, which
leaves the call open so there is nothing to refuse. Those calls run.

The annotation names ``_Hidden``, which is bound nowhere at runtime: what a
``TypedDict`` imported under ``if TYPE_CHECKING:`` is when the call is
dispatched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from typing_extensions import Unpack

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    UnknownArguments,
    dispatch_spec,
)
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db


class _ByPk(django_filters.FilterSet):
    pk = django_filters.NumberFilter(field_name="pk")


class _Titled(serializers.Serializer):
    title = serializers.CharField(required=False)


def _hidden_service(*, instance: Post, **extras: Unpack[_Hidden]) -> str:  # noqa: F821
    return "ran"


def _hidden_service_with_data(
    *,
    instance: Post,
    data: Any,
    **extras: Unpack[_Hidden],  # noqa: F821
) -> str:
    return "ran"


def _plain_service(*, instance: Post) -> str:
    return "ran"


def _closed_lookup(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all()


def _open_lookup(*, pk: int, **filters: Any) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _hidden_lookup(*, pk: int, **extras: Unpack[_Hidden]) -> QuerySet[Post]:  # noqa: F821
    return Post.objects.filter(pk=pk)


_RETRIEVE = SelectorKind.RETRIEVE
_CLOSED = SelectorSpec(kind=_RETRIEVE, selector=_closed_lookup)
_FILTERED = SelectorSpec(kind=_RETRIEVE, selector=_all_posts, filter_set=_ByPk)
_OPEN = SelectorSpec(kind=_RETRIEVE, selector=_open_lookup)
_OPEN_AND_FILTERED = SelectorSpec(kind=_RETRIEVE, selector=_open_lookup, filter_set=_ByPk)

_AUTO = ArgumentBinding.AUTO
_BUNDLE = ArgumentBinding.BUNDLE
_SPREAD = ArgumentBinding.SPREAD_AUTHOR_WINS
_EVERY = {_AUTO, _BUNDLE, _SPREAD}


def _service(service: Any, lookup: SelectorSpec[Any, Any], **changes: Any) -> Callable[[], Any]:
    return lambda: ServiceSpec(
        service=service, instance_selector_spec=lookup, atomic=False, **changes
    )


# case -> (spec factory, the bindings the dispatch raises under)
_CASES: dict[str, tuple[Callable[[], Any], set[ArgumentBinding]]] = {
    "a top-level selector": (
        lambda: SelectorSpec(kind=SelectorKind.LIST, selector=_hidden_lookup),
        _EVERY,
    ),
    "a closed service's own lookup": (
        _service(_plain_service, SelectorSpec(kind=_RETRIEVE, selector=_hidden_lookup)),
        _EVERY,
    ),
    "a closed service's own filtered lookup": (
        _service(
            _plain_service,
            SelectorSpec(kind=_RETRIEVE, selector=_hidden_lookup, filter_set=_ByPk),
        ),
        _EVERY,
    ),
    "a serializer-less service beside a closed lookup": (
        _service(_hidden_service, _CLOSED),
        {_SPREAD},
    ),
    "a serializer-less service beside a filtered lookup": (
        _service(_hidden_service, _FILTERED),
        {_SPREAD},
    ),
    "a serializer-less service beside an open lookup": (
        _service(_hidden_service, _OPEN),
        set(),
    ),
    "a serializer-less service beside an open filtered lookup": (
        _service(_hidden_service, _OPEN_AND_FILTERED),
        set(),
    ),
    "a service with an input serializer beside a closed lookup": (
        _service(_hidden_service_with_data, _CLOSED, input_serializer=_Titled),
        set(),
    ),
    "a service with an input serializer beside a filtered lookup": (
        _service(_hidden_service_with_data, _FILTERED, input_serializer=_Titled),
        set(),
    ),
}


def _live_view() -> ServiceUpdateView:
    """A live view as DRF builds one per request, for a transport to dispatch."""
    view = ServiceUpdateView()
    view.request = Request(APIRequestFactory().put("/", {}, format="json"))
    view.kwargs = {}
    view.format_kwarg = None
    return view


@pytest.mark.parametrize("live", [False, True], ids=["off-http", "live-view"])
@pytest.mark.parametrize(
    "binding", [_AUTO, _BUNDLE, _SPREAD], ids=["auto", "bundle", "spread-author-wins"]
)
@pytest.mark.parametrize("case", list(_CASES))
def test_reject_raises_only_where_the_unresolvable_annotation_is_the_basis(
    case: str, binding: ArgumentBinding, live: bool
) -> None:
    make_spec, raises_under = _CASES[case]
    post = Post.objects.create(title="p")
    view = _live_view() if live else None

    def dispatch() -> Any:
        return dispatch_spec(
            make_spec(),
            user=None,
            params={"pk": post.pk},
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
            request=view.request if view is not None else None,
            view=view,
        )

    if binding in raises_under:
        with pytest.raises(ImproperlyConfigured, match="REJECT cannot be enforced"):
            dispatch()
    else:
        assert dispatch().value == "ran"
