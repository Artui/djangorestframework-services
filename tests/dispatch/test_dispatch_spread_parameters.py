"""A spread service with no input serializer declares its own parameters as input.

A ``ServiceSpec`` with no ``input_serializer``, dispatched under a ``SPREAD_*``
binding, takes the caller's input as the service's own keyword parameters: there
is nothing else to read it. Those parameters are its declared input beside the
target lookup's keys, so every ``UnknownArguments`` policy delivers what the caller
sent for one, and a required one the caller left out is a missing argument under
every policy rather than an unknown one under ``REJECT``.

Every other spec keeps its declared set: ``BUNDLE``, a spec with an
``input_serializer`` and a ``many=True`` spec are pinned at the bottom.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core builds the service's pool on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ArgumentBinding,
    DispatchResult,
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_POLICIES = pytest.mark.parametrize(
    "policy", list(UnknownArguments), ids=[policy.name.lower() for policy in UnknownArguments]
)
_SPREADS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)
_AUTHOR_WINS = ArgumentBinding.SPREAD_AUTHOR_WINS
_REASON_MISSING = {"non_field_errors": ["Missing required argument(s): 'reason'."]}


def _refusal(dispatch: Dispatch, spec: Any, **kwargs: Any) -> Any:
    """The detail ``spec`` is refused with: unknown (``REJECT``) or missing alike."""
    with pytest.raises((ValidationError, ServiceValidationError)) as excinfo:
        dispatch(spec, **kwargs)
    return excinfo.value.detail


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _posts_titled(*, title: str) -> QuerySet[Post]:
    return Post.objects.filter(title=title)


_LOOKUP = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)


@pytest.fixture
def post() -> Post:
    return Post.objects.create(title="open")


# --- the parameter the caller sends, or leaves out ---------------------------


def _close(*, instance: Post, reason: str, note: str = "none") -> dict[str, Any]:
    return {"post": instance.pk, "reason": reason, "note": note}


_CLOSE = ServiceSpec(service=_close, instance_selector_spec=_LOOKUP, atomic=False)


@pytest.mark.django_db
@_CORES
@_SPREADS
@_POLICIES
def test_a_sent_parameter_is_delivered_under_every_policy(
    dispatch: Dispatch, binding: ArgumentBinding, policy: UnknownArguments, post: Post
) -> None:
    result = dispatch(
        _CLOSE,
        params={"pk": post.pk, "reason": "dup"},
        argument_binding=binding,
        unknown_arguments=policy,
    )

    assert result.value == {"post": post.pk, "reason": "dup", "note": "none"}


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_an_unsent_parameter_is_missing_under_every_policy(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    detail = _refusal(
        dispatch,
        _CLOSE,
        params={"pk": post.pk},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert detail == _REASON_MISSING


@pytest.mark.django_db
@_CORES
def test_a_key_nothing_declares_is_still_refused_under_reject(
    dispatch: Dispatch, post: Post
) -> None:
    detail = _refusal(
        dispatch,
        _CLOSE,
        params={"pk": post.pk, "reason": "dup", "colour": "red"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'colour'."]}


def _touch(*, instance: Post, note: str = "none") -> str:
    return note


_TOUCH = ServiceSpec(service=_touch, instance_selector_spec=_LOOKUP, atomic=False)


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_defaulted_parameter_is_delivered_when_sent(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    result = dispatch(
        _TOUCH,
        params={"pk": post.pk, "note": "late"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == "late"


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_defaulted_parameter_keeps_its_default_when_not_sent(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    result = dispatch(
        _TOUCH, params={"pk": post.pk}, argument_binding=_AUTHOR_WINS, unknown_arguments=policy
    )

    assert result.value == "none"


def _close_with_data(*, instance: Post, reason: str, data: Any = None) -> dict[str, Any]:
    return {"reason": reason, "data": data}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    ("policy", "params", "data"),
    [
        pytest.param(UnknownArguments.IGNORE, {"origin": "mcp"}, {"reason": "dup"}, id="ignore"),
        pytest.param(UnknownArguments.REJECT, {}, {"reason": "dup"}, id="reject"),
        pytest.param(
            UnknownArguments.PASSTHROUGH,
            {"origin": "mcp"},
            {"reason": "dup", "origin": "mcp"},
            id="passthrough",
        ),
    ],
)
def test_data_carries_the_parameters_beside_the_passthrough_extras(
    dispatch: Dispatch,
    policy: UnknownArguments,
    params: dict[str, Any],
    data: dict[str, Any],
    post: Post,
) -> None:
    """With no serializer, ``data`` is what the spread carries, as it is for a
    dict-validating serializer: the declared parameters, and under ``PASSTHROUGH``
    the extras too, which is everything ``data`` held before the parameters were
    declared."""
    spec = ServiceSpec(service=_close_with_data, instance_selector_spec=_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", **params},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"reason": "dup", "data": data}


def _provide_reason() -> dict[str, str]:
    return {"reason": "author"}


@pytest.mark.django_db
@_CORES
@_POLICIES
@pytest.mark.parametrize(
    ("binding", "reason"),
    [
        pytest.param(ArgumentBinding.SPREAD_AUTHOR_WINS, "author", id="author-wins"),
        pytest.param(ArgumentBinding.SPREAD_CALLER_WINS, "caller", id="caller-wins"),
    ],
)
def test_the_binding_still_ranks_a_parameter_against_the_provider(
    dispatch: Dispatch, policy: UnknownArguments, binding: ArgumentBinding, reason: str, post: Post
) -> None:
    spec = ServiceSpec(
        service=_close, instance_selector_spec=_LOOKUP, kwargs=_provide_reason, atomic=False
    )
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "caller"},
        argument_binding=binding,
        unknown_arguments=policy,
    )

    assert result.value["reason"] == reason


def _archive(*, collection: QuerySet[Post], reason: str) -> dict[str, Any]:
    return {"count": collection.count(), "reason": reason}


@pytest.mark.django_db
@_CORES
def test_the_rule_holds_beside_a_collection_lookup(dispatch: Dispatch, post: Post) -> None:
    spec = ServiceSpec(
        service=_archive,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_posts_titled),
        atomic=False,
    )
    result = dispatch(
        spec,
        params={"title": "open", "reason": "dup"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert result.value == {"count": 1, "reason": "dup"}


# --- what is never the caller's ------------------------------------------------


def _close_scoped(
    *, instance: Post, reason: str, team: Annotated[str, NotClientInput] = "own-team"
) -> dict[str, Any]:
    return {"reason": reason, "team": team}


_CLOSE_SCOPED = ServiceSpec(service=_close_scoped, instance_selector_spec=_LOOKUP, atomic=False)


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH], ids=["ignore", "passthrough"]
)
def test_a_hidden_parameter_the_caller_sends_is_not_delivered(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    result = dispatch(
        _CLOSE_SCOPED,
        params={"pk": post.pk, "reason": "dup", "team": "other-team"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"reason": "dup", "team": "own-team"}


@pytest.mark.django_db
@_CORES
def test_a_hidden_parameter_is_unknown_under_reject(dispatch: Dispatch, post: Post) -> None:
    detail = _refusal(
        dispatch,
        _CLOSE_SCOPED,
        params={"pk": post.pk, "reason": "dup", "team": "other-team"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'team'."]}


def _close_positionally(note: str = "none", /, *, instance: Post, reason: str) -> str:
    return note


@pytest.mark.django_db
@_CORES
def test_a_positional_only_parameter_is_unknown_under_reject(
    dispatch: Dispatch, post: Post
) -> None:
    spec = ServiceSpec(service=_close_positionally, instance_selector_spec=_LOOKUP, atomic=False)
    detail = _refusal(
        dispatch,
        spec,
        params={"pk": post.pk, "reason": "dup", "note": "late"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'note'."]}


def _close_as(*, instance: Post, reason: str, user: Any, data: Any = None) -> dict[str, Any]:
    return {"reason": reason, "user": user, "data": data}


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_reserved_seed_is_never_taken_from_the_caller(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    """The service declares ``user`` and ``data``, both reserved: the pool's seed
    fills ``user``, and neither the caller's ``user`` nor its ``data`` lands in the
    ``data`` the service receives."""
    spec = ServiceSpec(service=_close_as, instance_selector_spec=_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "user": "spoofed", "data": {"forged": True}},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"reason": "dup", "user": None, "data": {"reason": "dup"}}


def _close_for(*, instance: Post, reason: str, tenant: str, data: Any = None) -> dict[str, Any]:
    return {"reason": reason, "tenant": tenant, "data": data}


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_registered_seed_is_never_taken_from_the_caller(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    """A seed the caller's registry adds is reserved like a built-in one."""
    spec = ServiceSpec(service=_close_for, instance_selector_spec=_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "tenant": "spoofed"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        unknown_arguments=policy,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "seed-tenant"),
    )

    assert result.value == {"reason": "dup", "tenant": "seed-tenant", "data": {"reason": "dup"}}


# --- an open surface ---------------------------------------------------------------

_SEEN = ("pk", "reason", "colour", "user", "data")


def _close_open(
    *, instance: Post, team: Annotated[str, NotClientInput] = "own-team", **changes: Any
) -> dict[str, Any]:
    # A bare ``**kwargs`` receives the whole pool, seeds included, so only the
    # names the caller could have sent are reported.
    return {"team": team, **{key: changes[key] for key in _SEEN if key in changes}}


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_bare_var_keyword_takes_every_argument_but_the_lookups(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    """Open, so nothing is unknown: ``REJECT`` refuses nothing. Every key the caller
    sent reaches the service except the lookup's ``pk``, which the lookup consumed
    to find the ``instance`` the service receives; ``def update(*, instance,
    **changes)`` would otherwise write it onto the row. The caller's value for the
    hidden ``team`` is dropped too, and its ``user`` neither displaces the seed nor
    reaches ``data``."""
    spec = ServiceSpec(service=_close_open, instance_selector_spec=_LOOKUP, atomic=False)
    sent = {"reason": "dup", "colour": "red"}
    result = dispatch(
        spec,
        params={"pk": post.pk, **sent, "team": "other-team", "user": "spoofed"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"team": "own-team", **sent, "user": None, "data": sent}


def _apply(**changes: Any) -> dict[str, Any]:
    return {key: changes[key] for key in ("pk", "reason", "data") if key in changes}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "lookup",
    [None, SelectorSpec(kind=SelectorKind.RETRIEVE)],
    ids=["no-lookup", "lookup-without-selector"],
)
def test_a_bare_var_keyword_with_no_lookup_takes_every_argument(
    dispatch: Dispatch, lookup: Any
) -> None:
    """Nothing consumed ``pk``, so it is an argument like any other. A lookup spec
    with no ``selector`` resolves nothing, so it consumes nothing either."""
    spec = ServiceSpec(service=_apply, instance_selector_spec=lookup, atomic=False)
    sent = {"pk": 7, "reason": "dup"}
    result = dispatch(spec, params=sent, argument_binding=_AUTHOR_WINS)

    assert result.value == {**sent, "data": sent}


def _close_open_by_pk(*, instance: Post, pk: int, **changes: Any) -> dict[str, Any]:
    return {"pk": pk, **{key: changes[key] for key in ("reason", "data") if key in changes}}


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_a_service_naming_the_lookups_key_beside_var_keyword_receives_it(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    """The lookup's keys reach a service by name: naming ``pk`` asks for it, so it
    is taken, and lands in the named parameter rather than in ``**changes``."""
    spec = ServiceSpec(service=_close_open_by_pk, instance_selector_spec=_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {
        "pk": post.pk,
        "reason": "dup",
        "data": {"pk": post.pk, "reason": "dup"},
    }


if TYPE_CHECKING:
    # Only the type checker sees it, so the runtime cannot resolve the annotation
    # that names it: the routine ``from __future__ import annotations`` idiom.
    class _HiddenTenant(TypedDict, total=False):
        tenant: str


def _close_for_tenant(*, instance: Post, **extras: Unpack[_HiddenTenant]) -> str | None:
    return extras.get("tenant")


_CLOSE_FOR_TENANT = ServiceSpec(
    service=_close_for_tenant, instance_selector_spec=_LOOKUP, atomic=False
)


@pytest.mark.django_db
@_CORES
def test_an_unresolvable_var_keyword_makes_reject_unenforceable(
    dispatch: Dispatch, post: Post
) -> None:
    with pytest.raises(ImproperlyConfigured, match="REJECT cannot be enforced"):
        dispatch(
            _CLOSE_FOR_TENANT,
            params={"pk": post.pk},
            argument_binding=_AUTHOR_WINS,
            unknown_arguments=UnknownArguments.REJECT,
        )


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH], ids=["ignore", "passthrough"]
)
def test_an_unresolvable_var_keyword_is_open_to_the_permissive_policies(
    dispatch: Dispatch, policy: UnknownArguments, post: Post
) -> None:
    result = dispatch(
        _CLOSE_FOR_TENANT,
        params={"pk": post.pk, "tenant": "acme"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == "acme"


class _ByPk:
    """A duck-typed ``filter_set``, which makes the lookup's surface open."""

    def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
        self._qs = queryset.filter(pk=data["pk"])

    @property
    def qs(self) -> QuerySet[Post]:
        return self._qs


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all()


_OPEN_LOOKUP = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_all_posts, filter_set=_ByPk)


@pytest.mark.django_db
@_CORES
def test_an_open_lookup_still_delivers_the_services_parameters(
    dispatch: Dispatch, post: Post
) -> None:
    spec = ServiceSpec(service=_close, instance_selector_spec=_OPEN_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.IGNORE,
    )

    assert result.value == {"post": post.pk, "reason": "dup", "note": "none"}


@pytest.mark.django_db
@_CORES
def test_an_open_lookup_leaves_reject_enforceable_beside_an_unresolvable_service(
    dispatch: Dispatch, post: Post
) -> None:
    """The lookup already makes the set open, so the service's surface cannot
    change what ``REJECT`` refuses and is not read for it: ``declared_input_keys``
    reads the lookup first. Delivery still treats the service as open."""
    spec = ServiceSpec(service=_close_for_tenant, instance_selector_spec=_OPEN_LOOKUP, atomic=False)
    result = dispatch(
        spec,
        params={"pk": post.pk, "tenant": "acme"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert result.value == "acme"


# --- every other spec keeps its declared set ----------------------------------------
#
# These pass before the fix as well: they pin what the rule must not reach.


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "binding", [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE], ids=["auto", "bundle"]
)
def test_a_bundled_service_still_declares_only_the_lookup(
    dispatch: Dispatch, binding: ArgumentBinding, post: Post
) -> None:
    detail = _refusal(
        dispatch,
        _CLOSE,
        params={"pk": post.pk, "reason": "dup"},
        argument_binding=binding,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'reason'."]}


@pytest.mark.django_db
@_CORES
def test_a_bundled_service_still_spreads_nothing(dispatch: Dispatch, post: Post) -> None:
    detail = _refusal(
        dispatch,
        _CLOSE,
        params={"pk": post.pk, "reason": "dup"},
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    assert detail == _REASON_MISSING


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def _retitle(*, instance: Post, title: str, note: str = "none") -> dict[str, str]:
    return {"title": title, "note": note}


_RETITLE = ServiceSpec(
    service=_retitle,
    input_serializer=_TitleSerializer,
    instance_selector_spec=_LOOKUP,
    atomic=False,
)


@pytest.mark.django_db
@_CORES
def test_a_serializer_still_declares_the_input_under_reject(dispatch: Dispatch, post: Post) -> None:
    detail = _refusal(
        dispatch,
        _RETITLE,
        params={"pk": post.pk, "title": "x", "note": "late"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'note'."]}


@pytest.mark.django_db
@_CORES
def test_a_serializer_still_drops_a_parameter_it_does_not_declare(
    dispatch: Dispatch, post: Post
) -> None:
    result = dispatch(
        _RETITLE,
        params={"pk": post.pk, "title": "x", "note": "late"},
        argument_binding=_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.IGNORE,
    )

    assert result.value == {"title": "x", "note": "none"}


def _bulk(*, data: list[dict[str, Any]], reason: str = "none") -> int:
    return len(data)


@_CORES
def test_a_many_spec_still_refuses_a_parameter_inside_an_item(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_bulk, input_serializer=_TitleSerializer, many=True, atomic=False)
    detail = _refusal(
        dispatch,
        spec,
        params=[{"title": "a", "reason": "dup"}],
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert detail == {"non_field_errors": ["Unexpected argument(s): 'reason'."]}


@_CORES
@_SPREADS
@_POLICIES
def test_a_serializer_less_many_spec_is_never_dispatched_spread(
    dispatch: Dispatch, binding: ArgumentBinding, policy: UnknownArguments
) -> None:
    """Why ``declared_input_keys`` needs no ``many`` branch: the spreading binding is
    refused before an item is read, so no item ever reaches the service's own
    parameters, and the per-item check runs under ``AUTO`` alone."""

    def bulk(*, data: Any = None, reason: str = "none") -> int:
        raise AssertionError("a refused binding must not reach the service")

    spec = ServiceSpec(service=bulk, many=True, atomic=False)
    with pytest.raises(ValueError, match="many=True"):
        dispatch(
            spec, params=[{"reason": "dup"}], argument_binding=binding, unknown_arguments=policy
        )
