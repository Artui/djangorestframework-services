"""A ``functools.wraps`` decorator taking ``**kwargs`` may fill a parameter itself.

``inspect.signature`` follows ``__wrapped__``, so such a wrapper reports the
wrapped function's parameters, while the wrapper is what runs, and it may fill
any of them, as a decorator injecting a scope does. Which ones cannot be read,
so none is asked of the caller: one the wrapper leaves out fails as the
callable's own ``TypeError``, as on 0.57.0. A parameter the caller sends still
reaches it. Read for every callable whose parameters are the caller's
arguments: a selector, a list selector, a target lookup's selector and a
serializer-less service under ``SPREAD_AUTHOR_WINS``.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.service_specs.utils import left_to_the_callable
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()
_POLICIES = pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
_TENANT_MISSING = {"tenant": ["This field is required."]}


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])

_INJECTED: list[str] = []


def _injecting_tenant(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A decorator that supplies ``tenant`` itself, as a scoping decorator does."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("tenant", "injected")
        return fn(*args, **kwargs)

    return wrapper


def _injecting_tenant_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("tenant", "injected")
        return await fn(*args, **kwargs)

    return wrapper


@_injecting_tenant
def _scoped(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


@_injecting_tenant_async
async def _ascoped(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


@_injecting_tenant
def _scoped_post(*, pk: int, tenant: str) -> QuerySet[Post]:
    _INJECTED.append(tenant)
    return Post.objects.filter(pk=pk)


@_injecting_tenant
def _scoped_posts(*, tenant: str) -> QuerySet[Post]:
    _INJECTED.append(tenant)
    return Post.objects.filter(title=tenant)


@_injecting_tenant
def _scoped_retitle(*, title: str, tenant: str) -> dict[str, Any]:
    return {"title": title, "tenant": tenant}


def _touch(*, instance: Post) -> int:
    return instance.pk


def _touch_row(*, instance: Post) -> Post:
    return instance


def _plain(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


@_CORES
@_POLICIES
@pytest.mark.parametrize("selector", [_scoped, _ascoped], ids=["def", "async-def"])
def test_a_wrapper_that_fills_the_parameter_itself_is_not_refused(
    dispatch: Any, policy: UnknownArguments, selector: Any
) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)
    result = dispatch(spec, params={"pk": 1}, unknown_arguments=policy)
    assert result.value == {"pk": 1, "tenant": "injected"}


@_CORES
def test_a_value_the_caller_sends_still_reaches_the_wrapper(dispatch: Any) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped)
    result = dispatch(spec, params={"pk": 1, "tenant": "client"})
    assert result.value == {"pk": 1, "tenant": "client"}


@_CORES
def test_a_list_selector_s_wrapper_fills_the_parameter(dispatch: Any) -> None:
    Post.objects.create(title="injected")
    _INJECTED.clear()
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_scoped_posts)
    assert [post.title for post in dispatch(spec, params={}).value] == ["injected"]
    assert _INJECTED == ["injected"]


@pytest.mark.parametrize("query", ["", "?tenant=x"], ids=["no-query", "a-query-value"])
def test_over_http_a_selector_view_runs_a_wrapper_that_fills_the_parameter(query: str) -> None:
    """The query string never reaches a selector parameter, so ``tenant`` is the wrapper's."""
    post = Post.objects.create(title="p")
    _INJECTED.clear()

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_scoped_post, output_serializer=PostSerializer
        )

    response = _View.as_view()(_factory.get(f"/{query}"), pk=post.pk)
    assert response.status_code == 200
    assert response.data["id"] == post.pk
    assert _INJECTED == ["injected"]


def test_over_http_a_list_view_runs_a_wrapper_that_fills_the_parameter() -> None:
    Post.objects.create(title="injected")
    _INJECTED.clear()

    class _View(SelectorListView):
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_scoped_posts, output_serializer=PostSerializer
        )

    response = _View.as_view()(_factory.get("/"))
    assert response.status_code == 200
    assert [row["title"] for row in response.data] == ["injected"]
    assert _INJECTED == ["injected"]


_WRAPPED_LOOKUP = ServiceSpec(
    service=_touch,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped_post),
)


@_CORES
def test_a_target_lookup_s_wrapper_fills_the_parameter(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    _INJECTED.clear()
    assert dispatch(_WRAPPED_LOOKUP, params={"pk": post.pk}).value == post.pk
    assert _INJECTED == ["injected"]


def test_over_http_a_target_lookup_s_wrapper_fills_the_parameter() -> None:
    post = Post.objects.create(title="p")
    _INJECTED.clear()

    class _View(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(
            service=_touch_row,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped_post),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
            ),
        )

    response = _View.as_view()(_factory.put("/", {}, format="json"), pk=post.pk)
    assert response.status_code == 200
    assert response.data["id"] == post.pk
    assert _INJECTED == ["injected"]


@_CORES
@_POLICIES
def test_a_spread_service_s_wrapper_fills_the_parameter(
    dispatch: Any, policy: UnknownArguments
) -> None:
    spec = ServiceSpec(service=_scoped_retitle)
    result = dispatch(
        spec,
        params={"title": "t"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=policy,
    )
    assert result.value == {"title": "t", "tenant": "injected"}


def _refused_for_tenant(spec: Any) -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


def test_a_parameter_beside_var_keyword_is_still_missing() -> None:
    # ``**kwargs`` takes what arrives, but nothing arriving fills ``tenant``: the
    # callable that runs is the one declaring it, so the call would fail.
    def select(*, pk: int, tenant: str, **extras: Any) -> None: ...

    _refused_for_tenant(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select))


def test_a_wrapper_naming_its_own_parameters_is_still_refused() -> None:
    # The wrapper differs from what it wraps (``trace``), but takes no ``**kwargs``,
    # so it cannot fill ``tenant``: only what dispatch passes reaches it.
    def select(*, pk: int, tenant: str) -> None: ...

    @functools.wraps(select)
    def traced(*, pk: int, tenant: str, trace: bool = False) -> None: ...

    _refused_for_tenant(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=traced))


def test_a_staticmethod_is_read_through_not_skipped() -> None:
    # A ``staticmethod`` object carries ``__wrapped__`` too, but it is not a wrapper
    # that could fill anything: calling it calls ``_plain`` with what dispatch passes.
    _refused_for_tenant(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=staticmethod(_plain)))


def test_a_wrapper_that_fills_nothing_fails_as_its_own_type_error() -> None:
    @functools.wraps(_plain)
    def passing_through(*args: Any, **kwargs: Any) -> Any:
        return _plain(*args, **kwargs)

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=passing_through)
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={"pk": 1})


def test_the_compiled_selector_reports_what_it_leaves_to_the_wrapper() -> None:
    # The author's function keeps its own signature; the compiled callable reports
    # the default the kernel reads as "never missing", and runs the wrapper.
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped)
    compiled = kernel_spec(spec).selector
    assert str(inspect.signature(compiled)) == (
        "(*, pk: 'int' = <left to the callable>, tenant: 'str' = <left to the callable>)"
        " -> 'dict[str, Any]'"
    )
    assert str(inspect.signature(_scoped)) == "(*, pk: 'int', tenant: 'str') -> 'dict[str, Any]'"


@_injecting_tenant
def _gate(*, tenant: str) -> None:
    _INJECTED.append(tenant)


def _rows_or_default(*, tenant: str = "none") -> list[str]:
    return [tenant]


def _run_or_default(*, tenant: str = "none") -> str:
    return tenant


@_CORES
def test_a_selector_s_precondition_wrapper_fills_the_parameter(dispatch: Any) -> None:
    # ``tenant`` is the selector's read, so the precondition naming it could be
    # refused for it; its wrapper fills it instead.
    _INJECTED.clear()
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_rows_or_default, preconditions=[_gate])
    assert dispatch(spec, params={}).value == ["none"]
    assert _INJECTED == ["injected"]


@_CORES
def test_a_spread_service_s_precondition_wrapper_fills_the_parameter(dispatch: Any) -> None:
    _INJECTED.clear()
    spec = ServiceSpec(service=_run_or_default, preconditions=[_gate])
    result = dispatch(spec, params={}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS)
    assert result.value == "none"
    assert _INJECTED == ["injected"]


def test_only_a_named_required_parameter_is_left_to_the_callable() -> None:
    # A default stays the parameter's own, a positional-only parameter is never
    # passed by name, and a name not asked for keeps its requirement. Every
    # parameter dispatch passes by name is reported keyword-only, so ``b`` with
    # its default may precede ``d`` with none.
    def f(a: int, /, b: int, d: int, *, c: int = 1) -> None: ...

    left = left_to_the_callable(f, frozenset({"b", "c"}))
    assert str(inspect.signature(left)) == (
        "(a: 'int', /, *, b: 'int' = <left to the callable>, d: 'int', c: 'int' = 1) -> 'None'"
    )
    assert left(1, b=2, d=3) is None
    assert str(inspect.signature(left_to_the_callable(f))) == (
        "(a: 'int', /, *, b: 'int' = <left to the callable>, d: 'int' = <left to the callable>,"
        " c: 'int' = 1) -> 'None'"
    )
