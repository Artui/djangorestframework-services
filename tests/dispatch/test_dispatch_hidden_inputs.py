"""A caller's value for a ``NotClientInput`` key never reaches the callable.

The marker drops a key from the schema, and dispatch drops the caller's value for
it before the spread, on both cores and under every ``UnknownArguments`` policy.
The key can still be filled by a channel the caller does not control: a
``spec.kwargs`` provider, a route capture (``build_offline_context(kwargs=…)``), a
registered pool seed, or the parameter's own default.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core assembles its own pools and so strips on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
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
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.types.unset import UNSET
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


# --- a SelectorSpec's own selector, under each policy ---------------------


def _widgets(*, team: Annotated[str, NotClientInput] = "own-team", limit: int = 10) -> str:
    return team


def _widgets_open(*, team: Annotated[str, NotClientInput] = "own-team", **filters: Any) -> str:
    return team


def _provide_team() -> dict[str, str]:
    return {"team": "provider-team"}


_CLOSED = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widgets)
_OPEN = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widgets_open)
_PROVIDED = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widgets, kwargs=_provide_team)


@_CORES
def test_reject_still_refuses_a_hidden_key_on_a_closed_selector(dispatch: Dispatch) -> None:
    """The loud refusal stays where the declared set allows one."""
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_CLOSED, params={"team": "other-team"}, unknown_arguments=UnknownArguments.REJECT)
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'team'."]}


@_CORES
@pytest.mark.parametrize(
    ("spec", "policy", "expected"),
    [
        pytest.param(_CLOSED, UnknownArguments.IGNORE, "own-team", id="closed-ignore"),
        pytest.param(_CLOSED, UnknownArguments.PASSTHROUGH, "own-team", id="closed-passthrough"),
        pytest.param(_OPEN, UnknownArguments.REJECT, "own-team", id="open-reject"),
        pytest.param(_OPEN, UnknownArguments.IGNORE, "own-team", id="open-ignore"),
        pytest.param(_OPEN, UnknownArguments.PASSTHROUGH, "own-team", id="open-passthrough"),
        pytest.param(_PROVIDED, UnknownArguments.IGNORE, "provider-team", id="provided-ignore"),
    ],
)
def test_a_callers_hidden_key_never_reaches_the_selector(
    dispatch: Dispatch, spec: SelectorSpec[Any, Any], policy: UnknownArguments, expected: str
) -> None:
    result = dispatch(spec, params={"team": "other-team"}, unknown_arguments=policy)

    assert result.value == expected


@_CORES
def test_caller_wins_cannot_outrank_a_provider_with_a_hidden_key(dispatch: Dispatch) -> None:
    """``SPREAD_CALLER_WINS`` ranks the spread over the provider, so before the strip a
    caller's value for a hidden key beat the author's. The strip happens before the
    ranking, so there is no caller value left to rank."""
    result = dispatch(
        _PROVIDED,
        params={"team": "other-team"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
    )

    assert result.value == "provider-team"


def _colour_and_team(
    *, team: Annotated[str, NotClientInput] = "own-team", **filters: Any
) -> dict[str, Any]:
    return {"team": team, "colour": filters.get("colour")}


@_CORES
def test_only_the_hidden_key_is_dropped_from_an_open_selector(dispatch: Dispatch) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_colour_and_team)

    result = dispatch(spec, params={"team": "other-team", "colour": "red"})

    assert result.value == {"team": "own-team", "colour": "red"}


class _WidgetExtras(TypedDict, total=False):
    project_pk: int
    team_role: Annotated[str, NotClientInput]


def _list_widgets(**extras: Unpack[_WidgetExtras]) -> dict[str, Any]:
    return {key: extras.get(key) for key in ("project_pk", "team_role")}


def _provide_role() -> dict[str, str]:
    return {"team_role": "member"}


@_CORES
@pytest.mark.parametrize(
    ("provider", "expected_role"),
    [
        pytest.param(None, None, id="no-provider"),
        pytest.param(_provide_role, "member", id="provider"),
    ],
)
def test_the_recipes_typed_dict_extras_drop_the_callers_hidden_key(
    dispatch: Dispatch, provider: Any, expected_role: str | None
) -> None:
    """The shape ``docs/recipes/off-http-inputs.md`` shows: the marker on a key of
    the ``TypedDict`` an ``Unpack`` expands, read through ``**extras``."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_list_widgets, kwargs=provider)

    result = dispatch(spec, params={"project_pk": 7, "team_role": "admin"})

    assert result.value == {"project_pk": 7, "team_role": expected_role}


@_CORES
def test_a_route_capture_still_fills_a_hidden_key(dispatch: Dispatch) -> None:
    """A route capture is the transport's value, not the caller's."""
    context = build_offline_context(None, kwargs={"team": "route-team"})

    result = dispatch(
        _CLOSED, params={"team": "other-team"}, request=context.request, view=context.view
    )

    assert result.value == "route-team"


def _decline_team() -> dict[str, Any]:
    return {"team": UNSET}


@_CORES
def test_a_declining_provider_leaves_the_default_not_the_callers_value(
    dispatch: Dispatch,
) -> None:
    """Declining with ``UNSET`` removes the provider's key from the pool. Before the
    strip that let the caller's value through; now the default is what is left."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widgets, kwargs=_decline_team)

    result = dispatch(spec, params={"team": "other-team"})

    assert result.value == "own-team"


@_CORES
def test_a_registered_pool_seed_still_fills_a_hidden_key(dispatch: Dispatch) -> None:
    seeds = DEFAULT_POOL_SEEDS.extend(team=lambda: "seed-team")

    result = dispatch(_CLOSED, params={"team": "other-team"}, pool_seeds=seeds)

    assert result.value == "seed-team"


# --- the service path ----------------------------------------------------


def _record_team(
    *, team: Annotated[str, NotClientInput] = "own-team", data: Any = None
) -> dict[str, Any]:
    return {"team": team, "data": None if data is None else dict(data)}


_SERVICE = ServiceSpec(service=_record_team, atomic=False)


@_CORES
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)
def test_passthrough_does_not_spread_a_hidden_key_into_a_service(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    result = dispatch(
        _SERVICE,
        params={"team": "other-team", "note": "kept"},
        argument_binding=binding,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    assert result.value == {"team": "own-team", "data": {"note": "kept"}}


@_CORES
def test_passthrough_does_not_fold_a_hidden_key_into_a_bundled_services_data(
    dispatch: Dispatch,
) -> None:
    """Under ``BUNDLE`` nothing is spread, but ``PASSTHROUGH`` folds its extras into
    ``data``. A hidden key is not one of them: the callable said the caller does not
    supply it."""
    result = dispatch(
        _SERVICE,
        params={"team": "other-team", "note": "kept"},
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    assert result.value == {"team": "own-team", "data": {"note": "kept"}}


class _TeamSerializer(serializers.Serializer):
    team = serializers.CharField()


@_CORES
def test_a_serializer_field_named_like_a_hidden_parameter_stays_in_data_only(
    dispatch: Dispatch,
) -> None:
    """The serializer declares ``team`` as input, so its validated payload carries it
    in ``data``. The service's own ``team`` parameter is still not filled from it."""
    spec = ServiceSpec(service=_record_team, input_serializer=_TeamSerializer, atomic=False)

    result = dispatch(
        spec,
        params={"team": "other-team"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert result.value == {"team": "own-team", "data": {"team": "other-team"}}


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def _record_team_and_items(
    *, data: list[Any], team: Annotated[str, NotClientInput] = "own-team"
) -> dict[str, Any]:
    return {"team": team, "data": [dict(item) for item in data]}


@_CORES
def test_a_many_services_items_keep_a_hidden_key(dispatch: Dispatch) -> None:
    """A ``many=True`` service receives its items inside one ``data`` list and
    spreads nothing, so an item's key is never one of its parameters."""
    spec = ServiceSpec(
        service=_record_team_and_items, input_serializer=_TitleSerializer, many=True, atomic=False
    )

    result = dispatch(
        spec,
        params=[{"title": "x", "team": "other-team"}],
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    assert result.value == {"team": "own-team", "data": [{"title": "x", "team": "other-team"}]}


# --- a service's target lookups ------------------------------------------
#
# Both lookups build their pool from the caller's params directly rather than
# through ``merge_arguments``, so each strips on its own -- on both cores.


_SEEN: list[str] = []


def _post_for_team(*, pk: int, team: Annotated[str, NotClientInput] = "own-team") -> QuerySet[Post]:
    _SEEN.append(team)
    return Post.objects.filter(pk=pk)


def _posts_for_team(*, team: Annotated[str, NotClientInput] = "own-team") -> QuerySet[Post]:
    _SEEN.append(team)
    return Post.objects.order_by("id")


def _touch(*, instance: Post) -> Post:
    return instance


def _count(*, collection: QuerySet[Post]) -> int:
    return collection.count()


_INSTANCE_LOOKUP = ServiceSpec(
    service=_touch,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_for_team),
    atomic=False,
)
_COLLECTION_LOOKUP = ServiceSpec(
    service=_count,
    collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_posts_for_team),
    atomic=False,
)


@pytest.fixture
def seen() -> list[str]:
    _SEEN.clear()
    return _SEEN


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "policy",
    [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH],
    ids=["ignore", "passthrough"],
)
def test_a_callers_hidden_key_never_reaches_the_instance_lookup(
    dispatch: Dispatch, policy: UnknownArguments, seen: list[str]
) -> None:
    post = Post.objects.create(title="p")

    result = dispatch(
        _INSTANCE_LOOKUP, params={"pk": post.pk, "team": "other-team"}, unknown_arguments=policy
    )

    assert result.kind == "instance"
    assert seen == ["own-team"]


@pytest.mark.django_db
@_CORES
def test_a_callers_hidden_key_never_reaches_the_collection_lookup(
    dispatch: Dispatch, seen: list[str]
) -> None:
    Post.objects.create(title="p")

    result = dispatch(_COLLECTION_LOOKUP, params={"team": "other-team"})

    assert result.value == 1
    assert seen == ["own-team"]


@pytest.mark.django_db
@_CORES
def test_a_route_capture_still_fills_a_lookups_hidden_key(
    dispatch: Dispatch, seen: list[str]
) -> None:
    post = Post.objects.create(title="p")
    context = build_offline_context(None, kwargs={"team": "route-team"})

    dispatch(
        _INSTANCE_LOOKUP,
        params={"pk": post.pk, "team": "other-team"},
        request=context.request,
        view=context.view,
    )

    assert seen == ["route-team"]
