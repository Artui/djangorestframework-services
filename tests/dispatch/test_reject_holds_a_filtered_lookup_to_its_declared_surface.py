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

Under ``SPREAD_AUTHOR_WINS`` the service's own parameters are the caller's
input too, and the same key is refused for exactly two shapes of service,
where 0.57.0 ran them: a serializer-less service with named parameters, when
the key is none of them, and any service with an ``input_serializer``, whether
its signature is closed or takes a bare ``**extras``. A serializer-less
service whose only parameter is a bare ``**kwargs`` declares none, and the key
is dropped. A key that is one of a serializer-less service's parameters is
delivered.

Each condition has a test that fails without it: the policy
(``test_ignore_still_drops_the_key``) and the lookup's closed selector
(``test_a_lookup_whose_selector_takes_bare_kwargs_declares_no_set``).
``test_a_spreading_binding_hands_a_bare_kwargs_service_nothing`` pins what the
same call does with a bare ``**kwargs`` service under the spreading binding, and
the ``test_under_a_spreading_binding_*`` tests the shapes it refuses and the one
parameter it admits.
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
    # Under ``SPREAD_AUTHOR_WINS`` a serializer-less service's parameters are its
    # input, and a bare ``**kwargs`` declares none, so the key is dropped and not
    # refused. A service with an input serializer is the other case, below.
    post = Post.objects.create(title="p")

    value = _dispatch(
        _spec(kind),
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert value == "none"


def _reason_of_the_row(*, instance: Post, reason: str = "none") -> Any:
    return reason


def _reason_of_the_set(*, collection: Any, reason: str = "none") -> Any:
    return reason


def _tenant_of_the_row_by_name(*, instance: Post, tenant: str = "none") -> Any:
    return tenant


def _tenant_of_the_set_by_name(*, collection: Any, tenant: str = "none") -> Any:
    return tenant


def _closed_beside_data(*, instance: Post, data: Any) -> Any:
    return "ran"


def _closed_beside_data_of_the_set(*, collection: Any, data: Any) -> Any:
    return "ran"


def _tenant_beside_data_of_the_set(*, collection: Any, data: Any, **extras: Any) -> Any:
    return extras.get("tenant", "none")


def _title_beside_data(*, instance: Post, data: Any) -> Any:
    return data["title"]


def _title_beside_data_of_the_set(*, collection: Any, data: Any) -> Any:
    return data["title"]


def _tenant_by_name_beside_data(*, instance: Post, data: Any, tenant: str = "none") -> Any:
    return tenant


def _tenant_by_name_beside_data_of_the_set(
    *, collection: Any, data: Any, tenant: str = "none"
) -> Any:
    return tenant


def _spreading_spec(
    kind: str, service: Any, *, serializer: type[serializers.Serializer] | None = None
) -> ServiceSpec[Any, Any, Any]:
    if kind == "instance":
        lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_every_post, filter_set=_ByPk)
        return ServiceSpec(
            service=service,
            input_serializer=serializer,
            instance_selector_spec=lookup,
            atomic=False,
        )
    lookup = SelectorSpec(kind=SelectorKind.LIST, selector=_every_post, filter_set=_ByPk)
    return ServiceSpec(
        service=service, input_serializer=serializer, collection_selector_spec=lookup, atomic=False
    )


def _spread(
    kind: str,
    service: Any,
    *,
    live: bool,
    serializer: type[serializers.Serializer] | None = None,
    policy: UnknownArguments = UnknownArguments.REJECT,
) -> Any:
    post = Post.objects.create(title="p")
    return _dispatch(
        _spreading_spec(kind, service, serializer=serializer),
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        policy=policy,
    )


_NO_TENANT = {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}

# Each shape the spreading binding refuses beside a filtered lookup, as (lookup, service,
# serializer, the service's answer under ``IGNORE``): a serializer-less service whose named
# parameters leave the key out, and a service with an input serializer, closed, taking a
# bare ``**extras`` or naming the key as a parameter, for each lookup.
_REFUSED_UNDER_SPREAD = {
    "named-parameters-instance": ("instance", _reason_of_the_row, None, "none"),
    "named-parameters-collection": ("collection", _reason_of_the_set, None, "none"),
    "serializer-and-extras-instance": ("instance", _tenant_beside_data, _Titled, "none"),
    "serializer-and-extras-collection": (
        "collection",
        _tenant_beside_data_of_the_set,
        _Titled,
        "none",
    ),
    "serializer-closed-instance": ("instance", _closed_beside_data, _Titled, "ran"),
    "serializer-closed-collection": ("collection", _closed_beside_data_of_the_set, _Titled, "ran"),
    "serializer-and-a-parameter-instance": (
        "instance",
        _tenant_by_name_beside_data,
        _Titled,
        "none",
    ),
    "serializer-and-a-parameter-collection": (
        "collection",
        _tenant_by_name_beside_data_of_the_set,
        _Titled,
        "none",
    ),
}


@_LIVE
@pytest.mark.parametrize(
    ("kind", "service", "serializer", "_"),
    _REFUSED_UNDER_SPREAD.values(),
    ids=_REFUSED_UNDER_SPREAD.keys(),
)
def test_under_a_spreading_binding_reject_refuses_a_key_the_service_does_not_declare(
    kind: str, service: Any, serializer: Any, _: str, live: bool
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _spread(kind, service, live=live, serializer=serializer)

    assert excinfo.value.detail == _NO_TENANT


@_LIVE
@pytest.mark.parametrize(
    ("kind", "service", "serializer", "answer"),
    _REFUSED_UNDER_SPREAD.values(),
    ids=_REFUSED_UNDER_SPREAD.keys(),
)
def test_under_a_spreading_binding_ignore_still_drops_the_key_of_a_refused_shape(
    kind: str, service: Any, serializer: Any, answer: str, live: bool
) -> None:
    value = _spread(kind, service, live=live, serializer=serializer, policy=UnknownArguments.IGNORE)

    assert value == answer


@_LIVE
@pytest.mark.parametrize(
    ("service", "serializer", "answer"),
    [
        (_reason_of_the_row, None, "none"),
        (_tenant_beside_data, _Titled, "none"),
        (_closed_beside_data, _Titled, "ran"),
    ],
    ids=["named-parameters", "serializer-and-extras", "serializer-closed"],
)
def test_under_a_spreading_binding_a_lookup_taking_bare_kwargs_refuses_nothing(
    service: Any, serializer: Any, answer: str, live: bool
) -> None:
    """The lookup's closed selector is the condition the refusal above waits on."""
    post = Post.objects.create(title="p")
    lookup = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_every_post_with_filters, filter_set=_ByPk
    )
    spec = ServiceSpec(
        service=service, input_serializer=serializer, instance_selector_spec=lookup, atomic=False
    )

    value = _dispatch(
        spec,
        {"pk": post.pk, "tenant": "acme"},
        live=live,
        binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert value == answer


@_LIVE
def test_under_a_spreading_binding_a_serializer_service_refuses_the_key_beside_any_lookup(
    live: bool,
) -> None:
    """The recipe's sentence that a bare ``**extras`` service with an input serializer
    still refuses the key, which holds with no ``filter_set`` as well."""
    spec = ServiceSpec(
        service=_tenant_beside_data_of_the_set,
        input_serializer=_Titled,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_every_post),
        atomic=False,
    )

    with pytest.raises(ValidationError) as excinfo:
        _dispatch(spec, {"tenant": "acme"}, live=live, binding=ArgumentBinding.SPREAD_AUTHOR_WINS)

    assert excinfo.value.detail == _NO_TENANT


@_LIVE
@_KIND
def test_under_a_spreading_binding_reject_admits_a_field_of_the_input_serializer(
    kind: str, live: bool
) -> None:
    """The refusal is of a key outside the lookup's set *and* the serializer's fields."""
    post = Post.objects.create(title="p")
    service = _title_beside_data if kind == "instance" else _title_beside_data_of_the_set

    value = _dispatch(
        _spreading_spec(kind, service, serializer=_Titled),
        {"pk": post.pk, "title": "t"},
        live=live,
        binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert value == "t"


@_LIVE
@pytest.mark.parametrize(
    ("kind", "service"),
    [("instance", _tenant_of_the_row_by_name), ("collection", _tenant_of_the_set_by_name)],
    ids=["instance", "collection"],
)
def test_under_a_spreading_binding_reject_admits_a_parameter_of_the_service(
    kind: str, service: Any, live: bool
) -> None:
    assert _spread(kind, service, live=live) == "acme"
