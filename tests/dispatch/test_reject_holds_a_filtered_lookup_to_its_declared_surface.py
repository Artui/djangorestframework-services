"""``REJECT`` refuses a key a ``filter_set`` target lookup does not declare.

A target lookup (``instance_selector_spec`` or ``collection_selector_spec``)
that shapes its rows with a ``filter_set`` declares the filter's names and its
selector's parameters, and nothing else. Dispatched under
``UnknownArguments.REJECT`` and a binding that spreads nothing into the service
(``AUTO``, the default, or ``BUNDLE``), a caller's key outside that set is
refused as ``Unexpected argument(s)``, where 0.57.0 dropped it without a word.
A lookup whose selector takes a bare ``**kwargs`` declares no set, so nothing
is refused there; ``IGNORE`` still drops the key; and the filter's own name is
admitted.

Each condition has a test that fails without it: the policy
(``test_ignore_still_drops_the_key``) and the lookup's closed selector
(``test_a_lookup_whose_selector_takes_bare_kwargs_declares_no_set``).
``test_a_spreading_binding_hands_a_bare_kwargs_service_nothing`` pins what the
same call does under the binding that makes the service's parameters its input.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

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


def _every_post() -> QuerySet[Post]:
    return Post.objects.all()


def _every_post_with_filters(**filters: Any) -> QuerySet[Post]:
    return Post.objects.all()


def _tenant_of_the_row(*, instance: Post, **extras: Any) -> Any:
    return extras.get("tenant", "none")


def _tenant_of_the_set(*, collection: Any, **extras: Any) -> Any:
    return extras.get("tenant", "none")


def _tenant_beside_data(*, instance: Post, data: Any, **extras: Any) -> Any:
    return extras.get("tenant", "none")


def _live_view() -> ServiceUpdateView:
    """A live view as DRF builds one per request, for a transport to dispatch."""
    view = ServiceUpdateView()
    view.request = Request(APIRequestFactory().put("/", {}, format="json"))
    view.kwargs = {}
    view.format_kwarg = None
    return view


def _spec(
    kind: str, *, selector: Any = _every_post, with_serializer: bool = False
) -> ServiceSpec[Any, Any, Any]:
    if kind == "instance":
        lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, filter_set=_ByPk)
        return ServiceSpec(
            service=_tenant_beside_data if with_serializer else _tenant_of_the_row,
            input_serializer=_Titled if with_serializer else None,
            instance_selector_spec=lookup,
            atomic=False,
        )
    lookup = SelectorSpec(kind=SelectorKind.LIST, selector=selector, filter_set=_ByPk)
    return ServiceSpec(service=_tenant_of_the_set, collection_selector_spec=lookup, atomic=False)


def _dispatch(
    spec: ServiceSpec[Any, Any, Any],
    params: dict[str, Any],
    *,
    live: bool,
    policy: UnknownArguments = UnknownArguments.REJECT,
    binding: ArgumentBinding = ArgumentBinding.AUTO,
) -> Any:
    view = _live_view() if live else None
    return dispatch_spec(
        spec,
        user=None,
        params=params,
        argument_binding=binding,
        unknown_arguments=policy,
        request=view.request if view is not None else None,
        view=view,
    ).value


_LIVE = pytest.mark.parametrize("live", [False, True], ids=["off-http", "live-view"])
_KIND = pytest.mark.parametrize("kind", ["instance", "collection"])
_BINDING = pytest.mark.parametrize(
    "binding", [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE], ids=["auto", "bundle"]
)


@_LIVE
@_KIND
@_BINDING
def test_reject_refuses_a_key_the_filtered_lookup_does_not_declare(
    kind: str, binding: ArgumentBinding, live: bool
) -> None:
    post = Post.objects.create(title="p")

    with pytest.raises(ValidationError) as excinfo:
        _dispatch(_spec(kind), {"pk": post.pk, "tenant": "acme"}, live=live, binding=binding)

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}


@_LIVE
@_BINDING
def test_the_refusal_holds_beside_an_input_serializer(binding: ArgumentBinding, live: bool) -> None:
    post = Post.objects.create(title="p")

    with pytest.raises(ValidationError) as excinfo:
        _dispatch(
            _spec("instance", with_serializer=True),
            {"pk": post.pk, "tenant": "acme"},
            live=live,
            binding=binding,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}


@_LIVE
@_KIND
@_BINDING
def test_the_filters_own_name_is_admitted(kind: str, binding: ArgumentBinding, live: bool) -> None:
    post = Post.objects.create(title="p")

    assert _dispatch(_spec(kind), {"pk": post.pk}, live=live, binding=binding) == "none"


@_LIVE
@_KIND
@_BINDING
def test_ignore_still_drops_the_key(kind: str, binding: ArgumentBinding, live: bool) -> None:
    post = Post.objects.create(title="p")

    value = _dispatch(
        _spec(kind),
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=binding,
        policy=UnknownArguments.IGNORE,
    )

    assert value == "none"


@_LIVE
@_KIND
@_BINDING
def test_a_lookup_whose_selector_takes_bare_kwargs_declares_no_set(
    kind: str, binding: ArgumentBinding, live: bool
) -> None:
    post = Post.objects.create(title="p")

    value = _dispatch(
        _spec(kind, selector=_every_post_with_filters),
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=binding,
    )

    assert value == "none"


@_LIVE
@_KIND
def test_a_spreading_binding_hands_a_bare_kwargs_service_nothing(kind: str, live: bool) -> None:
    # Under ``SPREAD_AUTHOR_WINS`` the service's parameters are its input, and a
    # bare ``**kwargs`` declares none, so the key is dropped and not refused.
    post = Post.objects.create(title="p")

    value = _dispatch(
        _spec(kind),
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert value == "none"
