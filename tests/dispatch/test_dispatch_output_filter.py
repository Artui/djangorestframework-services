"""A service's output re-read filters on ``filter_data`` alone, unless the spec says not to.

Off HTTP the call's arguments are the service's input, and neither the input
schema nor ``UnknownArguments.REJECT`` knows the output selector's filter fields.
So the re-read's ``filter_set`` reads only an explicit ``filter_data`` -- the
channel the HTTP path fills from the query string -- and without one it is bound
to an empty mapping, as an HTTP request with no query string would bind it.
``ServiceSpec(output_filter_from_call=False)`` binds it to the empty mapping
whatever the call carries.

The target lookups are a different case: their filter fields are part of the
declared input. That is held by ``TestFilterDataOnTheTargetPath`` in
``test_dispatch_spec.py``.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, ClassVar

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet, Value
from django.http import QueryDict
from rest_framework.exceptions import ErrorDetail, ValidationError

from rest_framework_services import (
    DispatchResult,
    OfflineServiceView,
    QueryParam,
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
def test_explicit_filter_data_still_filters_the_re_read(dispatch: Dispatch) -> None:
    """The HTTP path passes the query string here, and so does a transport
    passing a tool's arguments as ``filter_data``."""
    result = dispatch(_SPEC, params={}, filter_data={"published": False})

    assert result.value.title == "draft"
    assert _PublishedOnly.bound_to == [{"published": False}]


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_filter_data_is_bound_as_it_was_passed(dispatch: Dispatch) -> None:
    """Whole, a ``QueryDict`` kept one: the ``FilterSet`` reads it as
    ``DjangoFilterBackend`` would read a query string, a repeated key included."""
    query = QueryDict("published=false&other=1")

    result = dispatch(_SPEC, params={}, filter_data=query)

    assert result.value.title == "draft"
    assert _PublishedOnly.bound_to == [query]


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_a_spec_can_keep_the_call_out_of_the_re_read(dispatch: Dispatch) -> None:
    """``output_filter_from_call=False``: ``filter_data`` narrows the target
    lookup, and the re-read is handed what the service produced and nothing of
    the call."""
    spec = dataclasses.replace(_SPEC, output_filter_from_call=False)

    result = dispatch(spec, params={}, filter_data={"published": False})

    assert result.value.title == "shipped"
    assert _PublishedOnly.bound_to == [{}]


class _OneAnnotated(django_filters.FilterSet):
    """Filters on ``n``, an annotation only ``extend_queryset`` adds."""

    n = django_filters.NumberFilter(field_name="n")

    class Meta:
        model = Post
        fields: ClassVar[list[str]] = []


def _touched(*, title: str) -> Post:
    post = Post.objects.get()
    post.title = title
    post.save(update_fields=["title"])
    return post


def _the_written_row(*, result: Post) -> QuerySet[Post]:
    return Post.objects.filter(pk=result.pk)


def _annotated(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
    return qs.annotate(n=Value(1))


def _annotating_spec(*, atomic: bool) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_touched,
        reads=[QueryParam("title", "string")],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_the_written_row,
            extend_queryset=_annotated,
            filter_set=_OneAnnotated,
        ),
        atomic=atomic,
    )


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(("n", "kept"), [("1", True), ("2", False)], ids=["matches", "misses"])
def test_the_filter_reads_what_extend_queryset_added(
    dispatch: Dispatch, n: str, kept: bool
) -> None:
    """``extend_queryset`` first, then the ``filter_set``: a filter on an
    annotation the callable adds raises ``FieldError`` the other way round."""
    Post.objects.create(title="orig")

    result = dispatch(
        _annotating_spec(atomic=False),
        params={"title": "new"},
        filter_data={"n": n},
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert (result.value is not None) is kept


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("atomic", [True, False], ids=["atomic", "not-atomic"])
def test_a_refused_filter_value_leaves_the_write_standing(dispatch: Dispatch, atomic: bool) -> None:
    """The re-read runs after the service's transaction, so a value the
    ``FilterSet`` refuses is DRF's 400 in the ``FilterSet``'s words while the
    write has committed, ``atomic`` or not."""
    Post.objects.create(title="orig")

    with pytest.raises(ValidationError) as caught:
        dispatch(_annotating_spec(atomic=atomic), params={"title": "new"}, filter_data={"n": "x"})

    assert caught.value.detail == {"n": [ErrorDetail("Enter a number.", code="invalid")]}
    assert caught.value.detail["n"][0].code == "invalid"
    assert Post.objects.get().title == "new"


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(("from_call", "title"), [(True, "draft"), (False, "shipped")])
@pytest.mark.usefixtures("posts")
def test_a_transport_s_offline_view_passes_filter_data_through(
    dispatch: Dispatch, from_call: bool, title: str
) -> None:
    """How drf-mcp and the agent transport call it: an ``OfflineServiceView``,
    the tool's arguments as ``filter_data``."""
    context = build_offline_context(None)
    spec = dataclasses.replace(_SPEC, output_filter_from_call=from_call)

    result = dispatch(
        spec,
        params={},
        request=context.request,
        view=context.view,
        filter_data={"published": False},
    )

    assert isinstance(context.view, OfflineServiceView)
    assert result.value.title == title


class _RequestScoped(_PublishedOnly):
    """Records the ``request`` its constructor is handed, as ``self.request``."""

    requests: ClassVar[list[Any]] = []

    @property
    def qs(self) -> QuerySet[Post]:
        _RequestScoped.requests.append(self.request)
        return super().qs


@pytest.mark.django_db
@pytest.mark.usefixtures("posts")
@_CORES
def test_a_request_scoped_filter_set_sees_the_call_s_request(dispatch: Dispatch) -> None:
    """As ``DjangoFilterBackend`` hands it: a ``FilterSet`` reading
    ``self.request`` finds the request the call was made with."""
    _RequestScoped.requests.clear()
    context = build_offline_context(None)
    spec = dataclasses.replace(
        _SPEC,
        output_selector_spec=dataclasses.replace(
            _SPEC.output_selector_spec, filter_set=_RequestScoped
        ),
    )

    dispatch(spec, params={}, request=context.request, view=context.view, filter_data={})

    assert _RequestScoped.requests == [context.request]
