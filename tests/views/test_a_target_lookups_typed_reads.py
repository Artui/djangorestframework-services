"""A target lookup's typed reads are coerced and flattened as the spec's own are.

A query string, a route capture and a form body carry strings, and a repeated
key carries every value, so each is read against the type its declaration
names: an integer and a boolean parsed from their string, an array as every
value the key repeats. A ``reads`` entry on the target lookup is the same
declaration as one on the spec, so its type is the one the value is read
against, on a live view and off HTTP, on either core.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    OfflineServiceView,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    UrlKwarg,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.registry.spec_registry import SpecRegistry
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

_factory = APIRequestFactory()

_QUERY = "/?n=3&flag=true&tags=a&tags=b"
_READ = {"n": 3, "flag": True, "tags": ["a", "b"], "org": 7}


class _BodyIn(serializers.Serializer):
    body = serializers.CharField(required=False)


def _spec(
    seen: dict[str, Any], *, spec_reads: Any = (), lookup_reads: Any = None
) -> ServiceSpec[Any, Any, Any]:
    def selector(
        *, pk: int, n: Any = None, flag: Any = None, tags: Any = None, org: Any = None
    ) -> Any:
        seen.update(n=n, flag=flag, tags=tags, org=org)
        return Post.objects.filter(pk=pk)

    def service(*, instance: Any, data: Any) -> Any:
        return instance

    reads = (
        QueryParam("n", type="integer"),
        QueryParam("flag", type="boolean"),
        QueryParam("tags", type="array"),
        UrlKwarg("org", type="integer"),
    )
    return ServiceSpec(
        service=service,
        input_serializer=_BodyIn,
        reads=spec_reads,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=reads if lookup_reads is None else lookup_reads,
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
        ),
        permission_classes=[],
        atomic=False,
    )


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@pytest.mark.django_db
def test_a_live_view_reads_them_from_its_query_string_and_route() -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = type(
        "_View",
        (ServiceUpdateView,),
        {"queryset": Post.objects.all(), "permission_classes": [], "spec": _spec(seen)},
    )

    response = view.as_view()(_factory.put(_QUERY, {}, format="json"), pk=post.pk, org="7")

    assert response.status_code == 200, response.data
    assert seen == _READ


@pytest.mark.django_db
@_CORES
def test_a_live_view_on_either_core_reads_them_alike(
    dispatch: Callable[..., DispatchResult],
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = ServiceUpdateView()
    view.request = Request(_factory.put(_QUERY, {}, format="json"), parsers=[JSONParser()])
    view.request.user = AnonymousUser()
    view.kwargs = {"pk": post.pk, "org": "7"}
    view.format_kwarg = None

    dispatch(_spec(seen), params=view.request.data, request=view.request, view=view)

    assert seen == _READ


@pytest.mark.django_db
@_CORES
def test_an_offline_views_query_string_and_route_are_read_alike(
    dispatch: Callable[..., DispatchResult],
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(
        request=Request(_factory.put(_QUERY)), kwargs={"pk": post.pk, "org": "7"}
    )

    dispatch(_spec(seen), params={"body": "x"}, request=view.request, view=view)

    assert seen == _READ


@pytest.mark.django_db
@_CORES
def test_a_form_body_sent_off_http_is_read_alike(
    dispatch: Callable[..., DispatchResult],
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})
    body = QueryDict("n=3&flag=true&tags=a&tags=b&org=7", mutable=True)

    dispatch(_spec(seen), params=body, request=view.request, view=view)

    assert seen == _READ
    assert view.kwargs == {"pk": post.pk, "org": 7}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("declared_on", ["spec", "lookup"])
def test_off_http_a_mapping_is_checked_as_the_spec_s_own_reads_are(
    dispatch: Callable[..., DispatchResult], declared_on: str
) -> None:
    """A value the type takes is read as it, and one it cannot take is refused under its name."""
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    spec = (
        _spec(seen, spec_reads=(QueryParam("n", type="integer"),), lookup_reads=())
        if declared_on == "spec"
        else _spec(seen)
    )

    def call(n: Any) -> None:
        view = OfflineServiceView(request=Request(_factory.put("/")), kwargs={"pk": post.pk})
        dispatch(spec, params={"n": n}, request=view.request, view=view)

    for sent in (3, "3"):
        call(sent)
        assert seen["n"] == 3

    with pytest.raises(ValidationError) as excinfo:
        call("three")
    assert list(excinfo.value.detail) == ["n"]


@pytest.mark.django_db
@_CORES
def test_a_name_the_spec_and_its_lookup_both_read_is_the_spec_s(
    dispatch: Callable[..., DispatchResult],
) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="a")
    view = OfflineServiceView(request=Request(_factory.put("/?n=3")), kwargs={"pk": post.pk})
    spec = _spec(
        seen,
        spec_reads=(QueryParam("n", type="integer"),),
        lookup_reads=(QueryParam("n", type="string"),),
    )

    dispatch(spec, params={}, request=view.request, view=view)

    assert seen["n"] == 3


def test_a_lookup_read_named_like_a_seed_is_refused() -> None:
    with pytest.raises(
        ImproperlyConfigured,
        match=r"ServiceSpec\.instance_selector_spec\.reads declares 'request', which dispatch fills",
    ):
        SpecRegistry().register("lookup", _spec({}, lookup_reads=(QueryParam("request"),)))
