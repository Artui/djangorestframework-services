"""Tests for ``build_offline_context``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from django.http import HttpRequest, QueryDict
from rest_framework import serializers

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    adispatch_spec,
    dispatch_spec,
    render_spec_output,
)
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_service_view import OfflineServiceView
from tests.testapp.models import Post

_USER = object()


def test_defaults_create_a_fresh_post_request() -> None:
    ctx = build_offline_context(_USER)
    assert isinstance(ctx, OfflineContext)
    assert isinstance(ctx.view, OfflineServiceView)
    assert ctx.user is _USER
    assert ctx.request.user is _USER
    assert ctx.request.method == "POST"
    assert ctx.request.data == {}
    assert ctx.view.action is None
    assert ctx.view.kwargs == {}
    assert ctx.view.request is ctx.request


def test_mapping_params_populate_request_data() -> None:
    ctx = build_offline_context(_USER, {"name": "x", "count": 2})
    assert ctx.request.data == {"name": "x", "count": 2}


def test_list_params_populate_request_data() -> None:
    ctx = build_offline_context(_USER, [{"a": 1}, {"a": 2}])
    assert ctx.request.data == [{"a": 1}, {"a": 2}]


def test_action_and_kwargs_flow_onto_the_view() -> None:
    ctx = build_offline_context(_USER, action="orders.create", kwargs={"pk": 7})
    assert ctx.view.action == "orders.create"
    assert ctx.view.kwargs == {"pk": 7}


def test_supplied_http_request_is_wrapped_and_forced_to_post() -> None:
    base = HttpRequest()
    base.method = "GET"
    base.META["HTTP_X_CUSTOM"] = "kept"
    ctx = build_offline_context(_USER, {"a": 1}, http_request=base)
    # A copy of the passed request is wrapped, and only the copy is forced to POST.
    assert ctx.request._request is not base
    assert ctx.request._request.method == "POST"
    assert base.method == "GET"
    # Pre-existing META survives, so headers a real transport carries are visible.
    assert ctx.request.META["HTTP_X_CUSTOM"] == "kept"
    assert ctx.request.data == {"a": 1}


def test_wrapping_a_real_wsgi_request_keeps_the_attributes_pickling_drops() -> None:
    # ``HttpRequest.__getstate__`` deliberately omits ``non_picklable_attrs``, so a
    # copy taken through ``copy.copy`` / ``__reduce_ex__`` silently loses ``environ``
    # and ``_stream``. ``WSGIRequest._get_scheme`` reads ``environ``, so the loss only
    # surfaces when something builds an absolute URI -- what a serializer carrying a
    # ``FileField`` or ``HyperlinkedIdentityField`` does on a perfectly ordinary read.
    from django.test import RequestFactory

    base = RequestFactory().post("/hook/")
    carried = set(base.__dict__)
    wrapped = build_offline_context(_USER, http_request=base).request._request
    # Nothing the caller's request held may be missing from the copy. Asserting the
    # whole set rather than naming ``environ`` keeps this true across Django versions:
    # which attributes are deemed non-picklable has changed, and the invariant has not.
    assert carried - set(wrapped.__dict__) == set()
    assert wrapped.build_absolute_uri("/x/") == "http://testserver/x/"


def test_wrapping_shares_meta_session_and_body_with_the_caller() -> None:
    base = HttpRequest()
    base.session = {"cart": ["a"]}  # ty: ignore[invalid-assignment]
    base._body = b"{}"
    ctx = build_offline_context(_USER, http_request=base)
    wrapped = ctx.request._request
    # The copy is shallow on purpose: everything the dispatched code reads through
    # the request keeps working, and a session write reaches the real session.
    assert wrapped.META is base.META
    assert wrapped.session is base.session
    assert wrapped._body is base._body
    wrapped.session["cart"].append("b")
    assert base.session["cart"] == ["a", "b"]


def test_wrapped_request_keeps_its_own_user() -> None:
    base = HttpRequest()
    caller_user = object()
    base.user = caller_user  # ty: ignore[unresolved-attribute]
    ctx = build_offline_context(_USER, http_request=base)
    # DRF's ``Request.user`` setter writes through to the request it wraps, so
    # without the copy this would reassign the live request's principal.
    assert ctx.request.user is _USER
    assert base.user is caller_user  # ty: ignore[unresolved-attribute]


def test_dispatching_twice_does_not_leak_query_params_between_calls() -> None:
    base = HttpRequest()
    base.GET = QueryDict("owner=real")
    build_offline_context(_USER, http_request=base, query_params={"owner": "alice"})
    second = build_offline_context(_USER, http_request=base)
    # The second dispatch sees the request's real query string, not the first
    # dispatch's params, and the caller's own ``GET`` is untouched throughout.
    assert second.request.query_params["owner"] == "real"
    assert base.GET["owner"] == "real"


def test_query_params_default_to_empty() -> None:
    ctx = build_offline_context(_USER)
    assert list(ctx.request.query_params.keys()) == []


def test_query_params_seed_the_request_get_and_stringify_scalars() -> None:
    ctx = build_offline_context(_USER, query_params={"query": "{id,name}", "page": 2})
    assert ctx.request.query_params["query"] == "{id,name}"
    # Scalars are stringified, mirroring HTTP query strings.
    assert ctx.request.query_params["page"] == "2"


def test_list_query_params_become_multivalued() -> None:
    ctx = build_offline_context(_USER, query_params={"status": ["open", "closed"]})
    assert ctx.request.query_params.getlist("status") == ["open", "closed"]


def test_seeded_query_params_are_immutable_like_a_real_get() -> None:
    ctx = build_offline_context(_USER, query_params={"a": "1"})
    with pytest.raises(AttributeError):
        ctx.request.query_params["b"] = "2"


def test_query_params_replace_a_wrapped_requests_get() -> None:
    base = HttpRequest()
    base.GET = QueryDict("a=1")
    ctx = build_offline_context(_USER, http_request=base, query_params={"b": "2"})
    assert "a" not in ctx.request.query_params
    assert ctx.request.query_params["b"] == "2"
    # The replacement is scoped to the wrapped copy.
    assert base.GET["a"] == "1"
    assert "b" not in base.GET


# --- what the synthetic query string reaches ------------------------------------
#
# It reaches whatever reads ``request.query_params`` itself: a serializer, or a
# request-scoped FilterSet. It is not what dispatch binds a ``filter_set`` to, so
# a caller wanting the query string to filter passes it as ``filter_data``.


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _published_from(queryset: QuerySet[Post], source: Any) -> QuerySet[Post]:
    raw = source.get("published")
    if raw is None:
        return queryset
    return queryset.filter(published=str(raw).lower() == "true")


class _ReadsItsData:
    """Duck-typed FilterSet narrowing by ``published`` in the data it is bound to."""

    def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
        self.qs = _published_from(queryset, data)


class _ReadsTheRequest:
    """A request-scoped FilterSet: it ignores its data and reads the query string,
    as one does that scopes itself behind ``DjangoFilterBackend``."""

    def __init__(self, *, data: Any, queryset: QuerySet[Post], request: Any) -> None:
        self.qs = _published_from(queryset, request.query_params)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _titles(result: DispatchResult) -> list[str]:
    return [post.title for post in result.value]


@pytest.fixture
def unpublished_requested() -> OfflineContext:
    Post.objects.create(title="shipped", published=True)
    Post.objects.create(title="draft", published=False)
    return build_offline_context(None, query_params={"published": "false"})


@pytest.mark.django_db
@_CORES
def test_a_request_scoped_filter_set_reads_the_synthetic_query_string(
    dispatch: Dispatch, unpublished_requested: OfflineContext
) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, filter_set=_ReadsTheRequest)
    context = unpublished_requested

    result = dispatch(spec, params={}, request=context.request, view=context.view)

    assert _titles(result) == ["draft"]


@pytest.mark.django_db
@_CORES
def test_a_filter_set_is_bound_to_params_not_the_synthetic_query_string(
    dispatch: Dispatch, unpublished_requested: OfflineContext
) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, filter_set=_ReadsItsData)
    context = unpublished_requested

    result = dispatch(spec, params={}, request=context.request, view=context.view)

    assert _titles(result) == ["shipped", "draft"]


@pytest.mark.django_db
@_CORES
def test_passing_the_query_string_as_filter_data_filters_on_it(
    dispatch: Dispatch, unpublished_requested: OfflineContext
) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, filter_set=_ReadsItsData)
    context = unpublished_requested

    result = dispatch(
        spec,
        params={},
        request=context.request,
        view=context.view,
        filter_data=context.request.query_params,
    )

    assert _titles(result) == ["draft"]


class _FieldsFromTheQueryString(serializers.Serializer):
    """Field selection read off the request, as django-restql does."""

    title = serializers.CharField()
    published = serializers.BooleanField()

    def to_representation(self, instance: Any) -> dict[str, Any]:
        wanted = self.context["request"].query_params.get("fields")
        rendered = super().to_representation(instance)
        return {key: value for key, value in rendered.items() if wanted is None or key == wanted}


@pytest.mark.django_db
def test_a_serializer_branching_on_query_params_reads_the_synthetic_query_string() -> None:
    post = Post.objects.create(title="shipped", published=True)
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_FieldsFromTheQueryString)
    context = build_offline_context(None, query_params={"fields": "title"})

    rendered = render_spec_output(spec, post, request=context.request, view=context.view)

    assert rendered == {"title": "shipped"}
