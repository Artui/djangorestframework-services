"""A service's output re-read filters on ``filter_data`` alone.

Off HTTP the call's arguments are the service's input, and neither the input
schema nor ``UnknownArguments.REJECT`` knows the output selector's filter fields.
So the re-read's ``filter_set`` reads only an explicit ``filter_data`` -- the
channel the HTTP path fills from the query string -- and without one it is bound
to an empty mapping, as an HTTP request with no query string would bind it.

The target lookups are a different case and keep their fallback to ``params``:
their filter fields are part of the declared input. That is held by
``TestFilterDataOnTheTargetPath`` in ``test_dispatch_spec.py``.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


class _PublishedOnly:
    """Duck-typed FilterSet narrowing by ``published``, as django-filter would.

    Records every mapping it is bound to, so a test can tell an empty filter
    source from the filter not running at all.
    """

    bound_to: list[Any] = []

    def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
        self._data = data
        self._queryset = queryset
        _PublishedOnly.bound_to.append(data)

    @property
    def qs(self) -> QuerySet[Post]:
        raw = self._data.get("published")
        if raw is None:
            return self._queryset
        return self._queryset.filter(published=str(raw).lower() == "true")


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
@pytest.mark.parametrize(
    "policy",
    [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH],
    ids=["ignore", "passthrough"],
)
def test_the_calls_arguments_do_not_filter_the_re_read(
    dispatch: Dispatch, policy: UnknownArguments
) -> None:
    result = dispatch(_SPEC, params={"published": "false"}, unknown_arguments=policy)

    assert result.value.title == "shipped"


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_without_filter_data_the_re_read_binds_an_empty_mapping(dispatch: Dispatch) -> None:
    """Bound, not skipped: a FilterSet that scopes in its own ``filter_queryset``
    still runs, and a bare call with no request does not fall back to reading
    ``request.query_params`` off a request that is not there."""
    dispatch(_SPEC, params={"published": "false"})

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
def test_explicit_filter_data_still_filters_the_re_read(dispatch: Dispatch) -> None:
    """The HTTP path passes the query string here, and that channel is unchanged."""
    result = dispatch(_SPEC, params={}, filter_data={"published": "false"})

    assert result.value.title == "draft"
