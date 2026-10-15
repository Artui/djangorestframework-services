"""A name a ``filter_set`` reads, and the selector's callables that take it too.

``filter_data`` and the query string feed the ``FilterSet`` alone: a selector
taking a bare ``**kwargs``, or a parameter named like a filter, never receives
them, and keeps its default. A caller's argument (``params``) is the
selector's as it was sent, a string where the filter reads a list, beside
whatever the filter reads. A route capture and a view hook's value reach the
selector and not the filter. Each of these is what 0.57.0 did.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    QueryParam,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Post

_factory = APIRequestFactory()

_NAMES = ("ordering", "published", "views_min", "title_in", "title")


class _Posts(django_filters.FilterSet):
    ordering = django_filters.OrderingFilter(fields=(("title", "name"),))
    views_min = django_filters.NumberFilter(field_name="views", lookup_expr="gte")
    title_in = django_filters.MultipleChoiceFilter(
        field_name="title", choices=[("a", "a"), ("b", "b"), ("c", "c")]
    )

    class Meta:
        model = Post
        fields = ["published", "title"]


class _Out(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["title"]


# Sent to every case: one value per kind of filter, each narrowing to b then a.
_SENT: dict[str, Any] = {
    "ordering": "-name",
    "published": True,
    "views_min": 1,
    "title_in": ["a", "b"],
}


def _greedy(seen: dict[str, Any]) -> Callable[..., Any]:
    def every_post(**kwargs: Any) -> Any:
        seen.update({name: value for name, value in kwargs.items() if name in _NAMES})
        return Post.objects.all()

    return every_post


def _explicit(seen: dict[str, Any]) -> Callable[..., Any]:
    def every_post(
        *,
        ordering: Any = None,
        published: Any = None,
        views_min: Any = None,
        title_in: Any = None,
    ) -> Any:
        seen.update(ordering=ordering, published=published, views_min=views_min, title_in=title_in)
        return Post.objects.all()

    return every_post


_DEFAULTS = {"ordering": None, "published": None, "views_min": None, "title_in": None}
_SELECTORS = pytest.mark.parametrize(
    ("selector", "untouched"),
    [(_greedy, {}), (_explicit, _DEFAULTS)],
    ids=["kwargs", "parameters"],
)


def _spec(selector: Callable[..., Any], **extra: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        filter_set=_Posts,
        output_serializer=_Out,
        permission_classes=[],
        **extra,
    )


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@pytest.fixture
def posts(db: Any) -> None:
    Post.objects.create(title="a", published=True, views=1)
    Post.objects.create(title="b", published=True, views=2)
    Post.objects.create(title="c", published=True, views=3)
    Post.objects.create(title="d", published=False, views=4)


def _titles(result: DispatchResult) -> list[str]:
    return [post.title for post in result.value]


def _view(spec: Any, **attrs: Any) -> Any:
    return type(
        "_View",
        (SelectorListView,),
        {"spec": spec, "permission_classes": [], "authentication_classes": [], **attrs},
    ).as_view()


_QUERY = "?ordering=-name&published=true&views_min=1&title_in=a&title_in=b"


@_CORES
@_SELECTORS
def test_filter_data_never_reaches_the_selector(
    dispatch: Callable[..., DispatchResult],
    selector: Callable[..., Any],
    untouched: dict[str, Any],
    posts: None,
) -> None:
    seen: dict[str, Any] = {}

    result = dispatch(_spec(selector(seen)), params={}, filter_data=_SENT)

    assert _titles(result) == ["b", "a"]
    assert seen == untouched


@_CORES
@_SELECTORS
def test_the_selector_takes_the_caller_s_argument_as_sent(
    dispatch: Callable[..., DispatchResult],
    selector: Callable[..., Any],
    untouched: dict[str, Any],
    posts: None,
) -> None:
    seen: dict[str, Any] = {}

    result = dispatch(_spec(selector(seen)), params=_SENT)

    assert _titles(result) == ["b", "a"]
    # As sent: the ordering a string, where the filter reads it as a list.
    assert seen == _SENT


@_CORES
def test_params_feed_the_selector_and_filter_data_the_filter(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}

    result = dispatch(
        _spec(_explicit(seen)), params={"ordering": "name"}, filter_data={"ordering": "-name"}
    )

    assert _titles(result) == ["d", "c", "b", "a"]
    assert seen == {**_DEFAULTS, "ordering": "name"}


@_CORES
def test_an_async_selector_is_handed_the_caller_s_argument_alone(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}

    async def every_post(**kwargs: Any) -> Any:
        return _greedy(seen)(**kwargs)

    spec = _spec(every_post)

    assert _titles(dispatch(spec, params={}, filter_data=_SENT)) == ["b", "a"]
    assert seen == {}
    assert _titles(dispatch(spec, params={"ordering": "-name"})) == ["d", "c", "b", "a"]
    assert seen == {"ordering": "-name"}


@_CORES
def test_a_precondition_is_handed_the_caller_s_argument_and_never_filter_data(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: list[Any] = []

    def check(*, ordering: Any = None, **rest: Any) -> None:
        seen.append((ordering, sorted(name for name in rest if name in _NAMES)))

    spec = _spec(_explicit({}), preconditions=[check])

    dispatch(spec, params={}, filter_data=_SENT)
    dispatch(spec, params={"ordering": "-name"})

    assert seen == [(None, []), ("-name", [])]


def test_over_a_live_view_the_query_string_reaches_no_selector(posts: None) -> None:
    for selector, untouched in ((_greedy, {}), (_explicit, _DEFAULTS)):
        seen: dict[str, Any] = {}

        response = _view(_spec(selector(seen)))(_factory.get("/" + _QUERY))

        assert response.status_code == 200, response.data
        assert [row["title"] for row in response.data] == ["b", "a"]
        assert seen == untouched


def test_over_a_live_view_a_route_capture_reaches_the_selector_as_captured(
    posts: None,
) -> None:
    seen: dict[str, Any] = {}

    response = _view(_spec(_explicit(seen)))(_factory.get("/name/?ordering=-name"), ordering="name")

    assert response.status_code == 200, response.data
    # The filter reads the query string, the selector the route's string.
    assert [row["title"] for row in response.data] == ["d", "c", "b", "a"]
    assert seen == {**_DEFAULTS, "ordering": "name"}


def test_over_a_live_view_a_hook_s_value_reaches_the_selector_and_not_the_filter(
    posts: None,
) -> None:
    seen: dict[str, Any] = {}

    def get_selector_kwargs(self: Any) -> dict[str, Any]:
        return {"title_in": ["c"]}

    view = _view(_spec(_explicit(seen)), get_selector_kwargs=get_selector_kwargs)
    response = view(_factory.get("/?title_in=a"))

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == ["a"]
    assert seen == {**_DEFAULTS, "title_in": ["c"]}


@_CORES
def test_the_provider_s_value_filters_and_reaches_the_selector(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    """The stated difference: the spec's provider feeds the filter as well."""
    seen: dict[str, Any] = {}
    spec = _spec(_explicit(seen), kwargs=lambda: {"title_in": ["c"]})

    assert _titles(dispatch(spec, params={}, filter_data={"title_in": ["a"]})) == ["c"]
    assert seen == {**_DEFAULTS, "title_in": ["c"]}


def test_a_query_param_named_like_a_filter_reaches_the_selector(posts: None) -> None:
    """A declared read the filter shares is still read for the selector over HTTP."""
    seen: dict[str, Any] = {}
    spec = _spec(_explicit(seen), reads=[QueryParam("ordering")])

    response = _view(spec)(_factory.get("/?ordering=-name"))

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == ["d", "c", "b", "a"]
    assert seen == {**_DEFAULTS, "ordering": ["-name"]}


def test_through_the_kernel_a_selector_still_reads_the_filter_s_value(posts: None) -> None:
    """Dispatched through the kernel's own ``dispatch``, a call has one argument channel."""
    seen: dict[str, Any] = {}
    compiled = kernel_spec(_spec(_explicit(seen)))
    principal = AnonymousUser()

    result = kernel_dispatch(
        compiled,
        principal=principal,
        arguments={"title_in": ["a"]},
        grant=Grant(compiled, principal),
    )

    assert [post.title for post in result.value] == ["a"]
    assert seen == {**_DEFAULTS, "title_in": ["a"]}


# ---------- a service's target lookup ----------


def _lookup(seen: dict[str, Any], **extra: Any) -> SelectorSpec[Any, Any]:
    def a_post(pk: int, *, ordering: Any = None, published: Any = None, **rest: Any) -> Any:
        seen.update(ordering=ordering, published=published)
        seen.update({name: value for name, value in rest.items() if name in _NAMES})
        return Post.objects.filter(pk=pk)

    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=a_post, filter_set=_Posts, **extra)


class _Rename(serializers.Serializer):
    body = serializers.CharField(required=False)


def _rename(*, instance: Any, body: str = "") -> Any:
    instance.body = body
    instance.save()
    return instance


def _service(seen: dict[str, Any], **extra: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_rename,
        input_serializer=_Rename,
        instance_selector_spec=_lookup(seen, **extra),
        permission_classes=[],
    )


@_CORES
def test_a_lookup_s_selector_takes_the_caller_s_argument_and_never_filter_data(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}
    pk = Post.objects.get(title="a").pk

    dispatch(_service(seen), params={"pk": pk}, filter_data={"published": True, "views_min": 1})
    assert seen == {"ordering": None, "published": None}

    seen.clear()
    dispatch(_service(seen), params={"pk": pk, "ordering": "name", "views_min": 1})
    assert seen == {"ordering": "name", "published": None, "views_min": 1}


def test_over_a_live_view_a_lookup_s_selector_takes_the_body_and_never_the_query(
    posts: None,
) -> None:
    seen: dict[str, Any] = {}
    pk = Post.objects.get(title="a").pk
    view = type(
        "_Update",
        (ServiceUpdateView,),
        {
            "spec": _service(seen),
            "output_serializer_class": _Out,
            "permission_classes": [],
            "authentication_classes": [],
            "queryset": Post.objects.all(),
        },
    ).as_view()

    response = view(
        _factory.patch("/?published=true", {"body": "x", "ordering": "-name"}, format="json"),
        pk=pk,
    )

    assert response.status_code == 200, response.data
    assert seen == {"ordering": "-name", "published": None}


@_CORES
def test_an_author_s_value_for_a_lookup_filter_reaches_the_lookup(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}
    pk = Post.objects.get(title="a").pk
    spec = ServiceSpec(
        service=_rename,
        input_serializer=_Rename,
        instance_selector_spec=_lookup(seen),
        kwargs=lambda: {"published": True},
        permission_classes=[],
    )

    dispatch(spec, params={"pk": pk}, filter_data={"published": False})

    assert seen == {"ordering": None, "published": True}


@_CORES
def test_a_lookup_s_provider_value_reaches_its_selector(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}
    pk = Post.objects.get(title="a").pk

    dispatch(
        _service(seen, kwargs=lambda: {"published": True}),
        params={"pk": pk, "published": False},
        filter_data={"title_in": ["a"]},
    )

    assert seen == {"ordering": None, "published": True}


@_CORES
def test_an_output_selector_is_handed_no_filter_value(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    """Never a difference: guarded here beside the rest."""
    seen: dict[str, Any] = {}

    def re_read(**kwargs: Any) -> Any:
        seen.update({name: value for name, value in kwargs.items() if name in _NAMES})
        return Post.objects.all()

    spec = ServiceSpec(
        service=lambda: Post.objects.get(title="a"),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=re_read, filter_set=_Posts
        ),
        permission_classes=[],
    )

    dispatch(spec, params={}, filter_data=_SENT)

    assert seen == {}


def test_through_the_kernel_a_lookup_s_selector_reads_its_provider_s_value(
    posts: None,
) -> None:
    """Outside an edge call the lookup's selector is handed the kernel's pool."""
    seen: dict[str, Any] = {}
    compiled = kernel_spec(_service(seen, kwargs=lambda: {"published": True}))
    principal = AnonymousUser()
    pk = Post.objects.get(title="a").pk

    kernel_dispatch(
        compiled, principal=principal, arguments={"pk": pk}, grant=Grant(compiled, principal)
    )

    assert seen == {"ordering": None, "published": True}


def test_a_kernel_call_inside_the_selector_is_handed_its_own_pool(posts: None) -> None:
    """What the edge prepared is for its own spec's callables, never another's."""
    inner_seen: dict[str, Any] = {}
    inner = kernel_spec(_spec(_explicit(inner_seen)))
    principal = AnonymousUser()

    def outer(**kwargs: Any) -> Any:
        kernel_dispatch(
            inner,
            principal=principal,
            arguments={"title_in": ["b"]},
            grant=Grant(inner, principal),
        )
        return Post.objects.all()

    dispatch_spec(_spec(outer), user=None, params={"title_in": ["a"]})

    assert inner_seen == {**_DEFAULTS, "title_in": ["b"]}


@_CORES
def test_arguments_that_are_no_object_are_refused_beside_a_filter(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_spec(_explicit({})), params=5)

    assert excinfo.value.detail == {"non_field_errors": ["Expected an object."]}


@_CORES
def test_a_hidden_parameter_beside_a_bare_kwargs_takes_no_client_value(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}

    def every_post(title: Annotated[str, NotClientInput] = "unset", **rest: Any) -> Any:
        seen.update(title=title, rest=sorted(rest.keys() & set(_NAMES)))
        return Post.objects.all()

    result = dispatch(_spec(every_post), params={"title": "a", "ordering": "-name"})

    assert _titles(result) == ["a"]
    assert seen == {"title": "unset", "rest": ["ordering"]}


@_CORES
def test_a_lookup_s_hidden_parameter_beside_a_bare_kwargs_takes_no_client_value(
    dispatch: Callable[..., DispatchResult], posts: None
) -> None:
    seen: dict[str, Any] = {}

    def a_post(pk: int, title: Annotated[str, NotClientInput] = "unset", **rest: Any) -> Any:
        seen.update(title=title, rest=sorted(rest.keys() & set(_NAMES)))
        return Post.objects.filter(pk=pk)

    spec = ServiceSpec(
        service=_rename,
        input_serializer=_Rename,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=a_post, filter_set=_Posts
        ),
        permission_classes=[],
    )

    dispatch(spec, params={"pk": Post.objects.get(title="a").pk, "title": "a"})

    assert seen == {"title": "unset", "rest": []}
