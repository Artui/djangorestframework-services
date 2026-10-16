"""``BUNDLE`` spreads none of the caller's input into a selector's parameters, as on 0.57.0.

What reaches a bundled selector is what the server gives it: a route capture,
a view hook's or the spec's ``kwargs`` provider's value, and the seeds. A
declared read (``reads=``) is declared input and still reaches it. The
caller's value for any other parameter is no argument, so ``REJECT`` does not
refuse it and the parameter keeps its default; a parameter with none that
nothing fills fails as the callable's own ``TypeError``, since no resend could
fill it. The filter still reads the caller's values, as it always did. A
selector view dispatches ``BUNDLE``, so over HTTP a selector parameter no route
captures and no read declares is the author's ``TypeError`` (a ``500``), never
a ``400`` no client could answer.
"""

from __future__ import annotations

from typing import Annotated, Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    InputRequired,
    QueryParam,
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    UnknownArguments,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()
BUNDLE = ArgumentBinding.BUNDLE
_POLICIES = pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT], ids=["ignore", "reject"]
)


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@_CORES
@_POLICIES
def test_bundle_on_selector_does_not_spread_params(dispatch: Any, policy: UnknownArguments) -> None:
    seen: dict[str, Any] = {}

    def with_default(*, wanted: str = "untouched") -> QuerySet[Post]:
        seen["wanted"] = wanted
        return Post.objects.none()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=with_default)
    dispatch(spec, params={"wanted": "client"}, argument_binding=BUNDLE, unknown_arguments=policy)
    assert seen["wanted"] == "untouched"


def _rows_for(*, tenant: str) -> list[str]:
    return [tenant]


def _tenant_rows_or_none(*, tenant: str = "none") -> list[str]:
    return [tenant]


def _precondition(*, tenant: str) -> None: ...


def _marked_rows(*, tenant: Annotated[str, InputRequired]) -> list[str]:
    return [tenant]


_BUNDLED_SELECTORS = [
    pytest.param(SelectorSpec(kind=SelectorKind.LIST, selector=_rows_for), id="selector"),
    pytest.param(
        SelectorSpec(
            kind=SelectorKind.LIST, selector=_tenant_rows_or_none, preconditions=[_precondition]
        ),
        id="selector-precondition",
    ),
    pytest.param(SelectorSpec(kind=SelectorKind.LIST, selector=_marked_rows), id="input-required"),
]


@_CORES
@_POLICIES
@pytest.mark.parametrize("spec", _BUNDLED_SELECTORS)
def test_a_bundled_selector_parameter_is_the_author_s_error(
    dispatch: Any, policy: UnknownArguments, spec: Any
) -> None:
    """The resend below carries ``tenant`` and the parameter is still unfilled, so
    naming it would send a client to send a key that never arrives."""
    for sent in ({}, {"tenant": "acme"}):
        with pytest.raises(TypeError, match="tenant"):
            dispatch(spec, params=sent, argument_binding=BUNDLE, unknown_arguments=policy)


def _sel_tenant(*, pk: int, tenant: str = "untouched") -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


@_CORES
@_POLICIES
def test_a_route_capture_still_reaches_a_bundled_selector(
    dispatch: Any, policy: UnknownArguments
) -> None:
    context = build_offline_context(None, kwargs={"pk": 7})
    result = dispatch(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel_tenant),
        params={"tenant": "client", "pk": 8},
        request=context.request,
        view=context.view,
        argument_binding=BUNDLE,
        unknown_arguments=policy,
    )
    assert result.value == {"pk": 7, "tenant": "untouched"}


@_CORES
def test_reject_still_refuses_a_key_no_parameter_names(dispatch: Any) -> None:
    context = build_offline_context(None, kwargs={"pk": 7})
    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel_tenant),
            params={"bogus": 1},
            request=context.request,
            view=context.view,
            argument_binding=BUNDLE,
            unknown_arguments=UnknownArguments.REJECT,
        )
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}


@_CORES
def test_a_provider_s_value_still_reaches_a_bundled_selector(dispatch: Any) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_sel_tenant, kwargs=lambda: {"pk": 3, "tenant": "s"}
    )
    result = dispatch(spec, params={"tenant": "client"}, argument_binding=BUNDLE)
    assert result.value == {"pk": 3, "tenant": "s"}


@_CORES
def test_a_declared_read_still_reaches_a_bundled_selector(dispatch: Any) -> None:
    context = build_offline_context(None, kwargs={"pk": 1})
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_sel_tenant, reads=[QueryParam("tenant")]
    )
    result = dispatch(
        spec,
        params={"tenant": "client"},
        request=context.request,
        view=context.view,
        argument_binding=BUNDLE,
    )
    assert result.value == {"pk": 1, "tenant": "client"}


class _ByTitle(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields = ["title"]


@_CORES
@_POLICIES
def test_the_filter_reads_the_caller_s_value_and_the_selector_parameter_does_not(
    dispatch: Any, policy: UnknownArguments
) -> None:
    Post.objects.create(title="a")
    Post.objects.create(title="b")
    seen: list[Any] = []

    def by_title(*, title: Any = None) -> QuerySet[Post]:
        seen.append(title)
        return Post.objects.order_by("title")

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=by_title, filter_set=_ByTitle)
    result = dispatch(
        spec, params={"title": "a"}, argument_binding=BUNDLE, unknown_arguments=policy
    )
    assert [post.title for post in result.value] == ["a"]
    assert seen == [None]


def _post_for_tenant(*, pk: int, tenant: str) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _posts_for_tenant(*, tenant: str) -> QuerySet[Post]:
    return Post.objects.all()


@pytest.mark.parametrize("query", ["", "?tenant=acme"], ids=["no-query", "a-query-value"])
def test_over_http_a_selector_parameter_nothing_fills_is_the_author_s_error(query: str) -> None:
    post = Post.objects.create(title="p")

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_post_for_tenant, output_serializer=PostSerializer
        )

    with pytest.raises(TypeError, match="tenant"):
        _View.as_view()(_factory.get(f"/{query}"), pk=post.pk)


@pytest.mark.parametrize("query", ["", "?tenant=acme"], ids=["no-query", "a-query-value"])
def test_over_http_a_list_selector_parameter_nothing_fills_is_the_author_s_error(
    query: str,
) -> None:
    class _View(SelectorListView):
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_posts_for_tenant, output_serializer=PostSerializer
        )

    with pytest.raises(TypeError, match="tenant"):
        _View.as_view()(_factory.get(f"/{query}"))


def test_over_http_a_declared_read_left_out_is_still_the_client_s_400() -> None:
    post = Post.objects.create(title="p")

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_post_for_tenant,
            output_serializer=PostSerializer,
            reads=[QueryParam("tenant")],
        )

    response = _View.as_view()(_factory.get("/"), pk=post.pk)
    assert response.status_code == 400
    assert response.data == {"tenant": ["This field is required."]}
    assert _View.as_view()(_factory.get("/?tenant=acme"), pk=post.pk).status_code == 200


@_CORES
def test_arguments_that_are_no_object_are_still_refused_under_bundle(dispatch: Any) -> None:
    # ``tenant`` is a parameter ``BUNDLE`` spreads nothing into, beside a filter.
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_rows_for, filter_set=_ByTitle)
    with pytest.raises(ValidationError):
        dispatch(spec, params=["a"], argument_binding=BUNDLE)
