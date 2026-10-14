"""A service's output re-read is not filtered by the call.

Off HTTP the call's arguments are the service's input, and neither the input
schema nor ``UnknownArguments.REJECT`` knows the output selector's filter fields.
The re-read is handed only what the service produced, so its ``filter_set`` is
bound to no arguments: not the call's, not an offline request's query string,
and not an explicit ``filter_data``, which narrows only the target lookup.

The target lookups are a different case: their filter fields are part of the
declared input. That is held by ``TestFilterDataOnTheTargetPath`` in
``test_dispatch_spec.py``.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _PublishedOnly(django_filters.FilterSet):
    """Narrows by ``published``, recording every mapping it is bound to, so a test
    can tell an empty filter source from the filter not running at all."""

    bound_to: ClassVar[list[Any]] = []

    published = django_filters.BooleanFilter()

    class Meta:
        model = Post
        fields = ["published"]

    def __init__(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        _PublishedOnly.bound_to.append(data)
        super().__init__(data, *args, **kwargs)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _touch() -> None:
    return None


_SPEC = ServiceSpec(
    service=_touch,
    output_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_all_posts, filter_set=_PublishedOnly
    ),
    atomic=False,
)


@pytest.fixture
def posts() -> None:
    _PublishedOnly.bound_to.clear()
    Post.objects.create(title="shipped", published=True)
    Post.objects.create(title="draft", published=False)


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_the_calls_arguments_do_not_filter_the_re_read(dispatch: Dispatch) -> None:
    result = dispatch(_SPEC, params={"published": False}, unknown_arguments=UnknownArguments.IGNORE)

    assert result.value.title == "shipped"


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_without_filter_data_the_re_read_binds_an_empty_mapping(dispatch: Dispatch) -> None:
    """Bound, not skipped: a FilterSet that scopes in its own ``filter_queryset``
    still runs, and a bare call with no request does not fall back to reading
    ``request.query_params`` off a request that is not there."""
    dispatch(_SPEC, params={"published": False})

    assert _PublishedOnly.bound_to == [{}]


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_an_offline_requests_query_string_is_not_read_either(dispatch: Dispatch) -> None:
    """``filter_data`` is the one channel. The synthetic request's query string is
    not a fallback for it, the same as the arguments are not."""
    context = build_offline_context(None, query_params={"published": "false"})

    result = dispatch(_SPEC, params={}, request=context.request, view=context.view)

    assert result.value.title == "shipped"


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_explicit_filter_data_does_not_filter_the_re_read(dispatch: Dispatch) -> None:
    """``filter_data`` narrows the target lookup's filter set; the re-read is
    handed what the service produced and nothing of the call."""
    result = dispatch(_SPEC, params={}, filter_data={"published": False})

    assert result.value.title == "shipped"
    assert _PublishedOnly.bound_to == [{}]
