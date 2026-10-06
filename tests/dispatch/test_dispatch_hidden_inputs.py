"""A caller's value for a ``NotClientInput`` key never reaches the callable.

The marker drops a key from the schema, and dispatch drops the caller's value for
it before the spread, on both cores and under every ``UnknownArguments`` policy.
The key can still be filled by a channel the caller does not control: a
``spec.kwargs`` provider, a route capture (``build_offline_context(kwargs=…)``), a
registered pool seed, a service's ``input_data``, or the parameter's own default.

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
    spec_to_json_schema,
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


def _required_team(*, team: Annotated[str, NotClientInput]) -> str:
    return team


@_CORES
def test_a_callers_value_for_a_required_hidden_parameter_leaves_the_callables_type_error(
    dispatch: Dispatch,
) -> None:
    """With no default to fall back on, dropping the caller's value is not silent: the
    callable raises its own ``TypeError``, which is not named as a missing argument
    because no value the caller sends could fill it."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_required_team)

    with pytest.raises(TypeError, match="team"):
        dispatch(spec, params={"team": "other-team"})


# --- a value input_data supplies -----------------------------------------
#
# ``input_data`` is the spec author's own code, merged onto the caller's input with
# its keys winning. So its value for a hidden key is not client input, and the strip
# leaves it: it reaches the service wherever an unmarked key's value would. A
# caller's value for the same key never arrives, because the merge has already
# replaced it with the server's. Each test runs with and without the caller's value.


def _server_team(**_: Any) -> dict[str, str]:
    return {"team": "server-team"}


def _team_beside_var_keyword(
    *, team: Annotated[str, NotClientInput] = "own-team", **_rest: Any
) -> str:
    return team


_CALLER_SENDS_TOO = pytest.mark.parametrize(
    "params", [{}, {"team": "other-team"}], ids=["server-only", "caller-too"]
)
_EVERY_POLICY = pytest.mark.parametrize(
    "policy", list(UnknownArguments), ids=lambda policy: policy.name.lower()
)


@_CORES
@_CALLER_SENDS_TOO
@_EVERY_POLICY
def test_input_data_fills_a_hidden_parameter_of_an_open_spread_service(
    dispatch: Dispatch, params: dict[str, Any], policy: UnknownArguments
) -> None:
    """A bare ``**kwargs`` opens the set, so no policy drops or refuses the key, and
    the strip is the only thing that could stop the server's value."""
    spec = ServiceSpec(service=_team_beside_var_keyword, input_data=_server_team, atomic=False)

    result = dispatch(
        spec,
        params=params,
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == "server-team"


@_CORES
@_CALLER_SENDS_TOO
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)
def test_passthrough_forwards_input_datas_hidden_key_to_a_closed_service(
    dispatch: Dispatch, params: dict[str, Any], binding: ArgumentBinding
) -> None:
    """Closed, the hidden key is undeclared, so ``PASSTHROUGH`` is what forwards it,
    into the spread and into ``data`` alike. ``SPREAD_CALLER_WINS`` has no caller
    value left to rank."""
    spec = ServiceSpec(service=_record_team, input_data=_server_team, atomic=False)

    result = dispatch(
        spec,
        params=params,
        argument_binding=binding,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    assert result.value == {"team": "server-team", "data": {"team": "server-team"}}


@_CORES
@_CALLER_SENDS_TOO
def test_passthrough_folds_input_datas_hidden_key_into_a_bundled_services_data(
    dispatch: Dispatch, params: dict[str, Any]
) -> None:
    """``BUNDLE`` spreads nothing, so the forwarded key reaches ``data`` alone and the
    parameter keeps its default, as an unmarked key's value would."""
    spec = ServiceSpec(service=_record_team, input_data=_server_team, atomic=False)

    result = dispatch(spec, params=params, unknown_arguments=UnknownArguments.PASSTHROUGH)

    assert result.value == {"team": "own-team", "data": {"team": "server-team"}}


@_CORES
@_CALLER_SENDS_TOO
@_EVERY_POLICY
def test_input_data_fills_a_hidden_parameter_through_a_serializer_field(
    dispatch: Dispatch, params: dict[str, Any], policy: UnknownArguments
) -> None:
    """The serializer declares ``team``, so every policy admits it, and the value it
    validates is the server's, merged in before validation. That is the one value a
    field of that name may hand the hidden parameter."""
    spec = ServiceSpec(
        service=_record_team,
        input_serializer=_TeamSerializer,
        input_data=_server_team,
        atomic=False,
    )

    result = dispatch(
        spec,
        params=params,
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"team": "server-team", "data": {"team": "server-team"}}


def _server_team_and_note(**_: Any) -> dict[str, str]:
    return {"team": "server-team", "note": "server-note"}


@_CORES
@_CALLER_SENDS_TOO
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["bundle", "author-wins"],
)
def test_reject_refuses_input_datas_keys_where_nothing_declares_them(
    dispatch: Dispatch, params: dict[str, Any], binding: ArgumentBinding
) -> None:
    """``REJECT`` judges the merged arguments, so it cannot tell the server's key
    from the caller's either. On a closed spec it refuses an ``input_data`` key that
    nothing declares, marked or not: the unmarked ``note`` is refused beside
    ``team``."""
    spec = ServiceSpec(service=_record_team, input_data=_server_team_and_note, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params=params,
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'note', 'team'."]}


@_CORES
@_CALLER_SENDS_TOO
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["bundle", "author-wins"],
)
def test_ignore_drops_input_datas_keys_where_nothing_declares_them(
    dispatch: Dispatch, params: dict[str, Any], binding: ArgumentBinding
) -> None:
    """The marker keeps ``team`` out of the declared set, so on a closed spec
    ``IGNORE`` drops the server's value as it drops the unmarked ``note``. It is the
    policy that drops it, not the strip, and the caller's value does not arrive
    in its place."""
    spec = ServiceSpec(service=_record_team, input_data=_server_team_and_note, atomic=False)

    result = dispatch(
        spec, params=params, argument_binding=binding, unknown_arguments=UnknownArguments.IGNORE
    )

    assert result.value == {"team": "own-team", "data": None}


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


# --- a key one callable hides is the server's for the whole call ---------------------
#
# The selector or service, each precondition and the target lookup share what the
# caller sent: the preconditions read the pool the selector or service is spread
# into, and the lookup reads the arguments a spread service takes. So a key any of
# them marks ``NotClientInput`` is dropped from the caller's input at every site.

_GATE_SEEN: list[str] = []


def _gate(*, tenant: Annotated[str, NotClientInput] = "own") -> None:
    _GATE_SEEN.append(tenant)


def _report(*, period: str = "q1") -> str:
    return period


def _titled(*, title: str) -> str:
    return title


def _open_changes(**changes: Any) -> dict[str, Any]:
    return dict(changes)


def _provide_tenant() -> dict[str, str]:
    return {"tenant": "server"}


@pytest.fixture
def gate_seen() -> list[str]:
    _GATE_SEEN.clear()
    return _GATE_SEEN


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    ("spec", "params", "kwargs", "expected"),
    [
        pytest.param(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_report, preconditions=[_gate]),
            {"tenant": "evil"},
            {},
            "own",
            id="selector-ignore",
        ),
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_report,
                preconditions=[_gate],
                kwargs=_provide_tenant,
            ),
            {"tenant": "evil"},
            {"argument_binding": ArgumentBinding.SPREAD_CALLER_WINS},
            "server",
            id="selector-caller-wins-over-a-provider",
        ),
        pytest.param(
            ServiceSpec(service=_titled, preconditions=[_gate], atomic=False),
            {"title": "t", "tenant": "evil"},
            {
                "argument_binding": ArgumentBinding.SPREAD_AUTHOR_WINS,
                "unknown_arguments": UnknownArguments.PASSTHROUGH,
            },
            "own",
            id="service-passthrough",
        ),
        pytest.param(
            ServiceSpec(service=_open_changes, preconditions=[_gate], atomic=False),
            {"tenant": "evil"},
            {"argument_binding": ArgumentBinding.SPREAD_AUTHOR_WINS},
            "own",
            id="open-spread-service-ignore",
        ),
    ],
)
def test_a_callers_value_never_reaches_a_preconditions_hidden_key(
    dispatch: Dispatch,
    spec: Any,
    params: dict[str, Any],
    kwargs: dict[str, Any],
    expected: str,
    gate_seen: list[str],
) -> None:
    dispatch(spec, params=params, **kwargs)

    assert gate_seen == [expected]


def _scoped_report(*, tenant: str = "default") -> str:
    return tenant


def _scoped_close(*, reason: str, tenant: str = "default") -> dict[str, str]:
    return {"reason": reason, "tenant": tenant}


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    ("spec", "binding", "params", "delivered"),
    [
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_scoped_report, preconditions=[_gate]
            ),
            ArgumentBinding.AUTO,
            {"tenant": "evil"},
            "default",
            id="selector",
        ),
        pytest.param(
            ServiceSpec(service=_scoped_close, preconditions=[_gate], atomic=False),
            ArgumentBinding.SPREAD_AUTHOR_WINS,
            {"reason": "r", "tenant": "evil"},
            {"reason": "r", "tenant": "default"},
            id="spread-service",
        ),
    ],
)
def test_a_key_a_precondition_hides_is_server_owned_for_the_whole_call(
    dispatch: Dispatch,
    spec: Any,
    binding: ArgumentBinding,
    params: dict[str, Any],
    delivered: Any,
    gate_seen: list[str],
) -> None:
    """The selector or service names ``tenant`` plainly, and its precondition hides it.

    The caller's value reaches neither, ``REJECT`` refuses it on the closed spec,
    and the input schema does not list it, so the three agree on one rule. Holds the
    preconditions' part of ``server_owned_keys`` at the strip, in
    ``declared_input_keys`` and in ``spec_to_json_schema``.
    """
    assert dispatch(spec, params=params, argument_binding=binding).value == delivered
    assert gate_seen == ["own"]

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params=params,
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
        )
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}

    schema = spec_to_json_schema(spec, argument_binding=binding)
    assert "tenant" not in schema.get("properties", {})


# --- a key the target lookup hides ---------------------------------------------------

_LOOKUP_SEEN: list[str] = []


def _row_in_tenant(*, pk: int, tenant: Annotated[str, NotClientInput] = "own") -> QuerySet[Post]:
    _LOOKUP_SEEN.append(tenant)
    return Post.objects.filter(pk=pk)


_TENANT_LOOKUP = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_row_in_tenant)


def _update(*, instance: Post, **changes: Any) -> dict[str, Any]:
    return dict(changes)


def _update_naming_tenant(
    *, instance: Post, tenant: str = "service-default", **changes: Any
) -> dict[str, Any]:
    return {"tenant": tenant, **changes}


def _update_closed(*, instance: Post, tenant: str = "service-default") -> str:
    return tenant


@pytest.mark.django_db(transaction=True)
@_CORES
@_EVERY_POLICY
def test_a_key_the_lookup_hides_never_reaches_an_open_service(
    dispatch: Dispatch, policy: UnknownArguments
) -> None:
    """``def update(*, instance, **changes)`` would ``setattr`` the caller's
    ``tenant`` onto the row, moving it to a tenant the lookup never scoped to."""
    post = Post.objects.create(title="p")
    spec = ServiceSpec(service=_update, instance_selector_spec=_TENANT_LOOKUP, atomic=False)

    result = dispatch(
        spec,
        params={"pk": post.pk, "tenant": "other", "title": "x"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert "tenant" not in result.value
    assert result.value["title"] == "x"


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, "service-default", id="the-default"),
        pytest.param({"kwargs": _provide_tenant}, "server", id="a-provider-under-caller-wins"),
    ],
)
def test_a_key_the_lookup_hides_never_reaches_a_service_naming_it(
    dispatch: Dispatch, kwargs: dict[str, Any], expected: str
) -> None:
    """Named, the key is the service's to receive, but only from the server. Holds
    the lookup's part of ``server_owned_keys`` at the strip: the name is not
    withheld, so only the strip keeps the caller's value out."""
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_update_naming_tenant,
        instance_selector_spec=_TENANT_LOOKUP,
        atomic=False,
        **kwargs,
    )

    result = dispatch(
        spec,
        params={"pk": post.pk, "tenant": "other", "title": "x"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
    )

    assert (result.value["tenant"], result.value["title"]) == (expected, "x")


@pytest.mark.django_db(transaction=True)
@_CORES
def test_a_key_the_lookup_hides_is_refused_beside_a_service_naming_it(
    dispatch: Dispatch,
) -> None:
    """Holds the lookup's part of ``server_owned_keys`` in ``declared_input_keys``
    and in the schema: the closed service names ``tenant``, so only the union
    keeps it undeclared."""
    post = Post.objects.create(title="p")
    spec = ServiceSpec(service=_update_closed, instance_selector_spec=_TENANT_LOOKUP, atomic=False)
    binding = ArgumentBinding.SPREAD_AUTHOR_WINS

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params={"pk": post.pk, "tenant": "other"},
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
        )
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}
    assert "tenant" not in spec_to_json_schema(spec, argument_binding=binding).get("properties", {})


def _server_tenant(**_: Any) -> dict[str, str]:
    return {"tenant": "server"}


@pytest.mark.django_db(transaction=True)
@_CORES
def test_a_key_the_lookup_hides_is_withheld_from_an_open_service_by_name(
    dispatch: Dispatch,
) -> None:
    """A hidden key is still the lookup's key, so it reaches an open service only by
    name, as ``pk`` does, even when the value is the server's own from
    ``input_data``. Fails if ``_lookup_parameter_names`` leaves the hidden keys out,
    which is what let the caller's value into ``changes`` before the strip read
    every callable's marker."""
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_update,
        instance_selector_spec=_TENANT_LOOKUP,
        input_data=_server_tenant,
        atomic=False,
    )

    result = dispatch(
        spec,
        params={"pk": post.pk, "title": "x"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert "tenant" not in result.value
    assert result.value["title"] == "x"


def _plain_row(*, pk: int, tenant: str = "lookup-default") -> QuerySet[Post]:
    _LOOKUP_SEEN.append(tenant)
    return Post.objects.filter(pk=pk)


def _touch_scoped(*, instance: Post, tenant: Annotated[str, NotClientInput] = "own") -> str:
    return tenant


def _plain_rows(*, tenant: str = "lookup-default") -> QuerySet[Post]:
    _LOOKUP_SEEN.append(tenant)
    return Post.objects.order_by("id")


def _count_scoped(
    *, collection: QuerySet[Post], tenant: Annotated[str, NotClientInput] = "own"
) -> str:
    return tenant


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize("lookup", ["instance", "collection"])
def test_a_key_the_service_hides_never_reaches_the_lookup(dispatch: Dispatch, lookup: str) -> None:
    """The lookup reads ``tenant`` plainly, but the service it resolves a row for hides
    it, so the row is never chosen by the caller's value either. One row per lookup,
    because each builds its own pool."""
    _LOOKUP_SEEN.clear()
    post = Post.objects.create(title="p")
    spec = (
        ServiceSpec(
            service=_touch_scoped,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_plain_row),
            atomic=False,
        )
        if lookup == "instance"
        else ServiceSpec(
            service=_count_scoped,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_plain_rows),
            atomic=False,
        )
    )

    result = dispatch(spec, params={"pk": post.pk, "tenant": "other"})

    assert result.value == "own"
    assert _LOOKUP_SEEN == ["lookup-default"]


def _data_seen(*, data: Any) -> dict[str, Any]:
    return dict(data)


def _row_data_seen(*, instance: Post, data: Any) -> dict[str, Any]:
    return dict(data)


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize("hider", ["precondition", "lookup"])
def test_a_key_another_callable_hides_never_reaches_the_services_data(
    dispatch: Dispatch, hider: str, gate_seen: list[str]
) -> None:
    """What ``PASSTHROUGH`` forwards lands in the service's ``data``, so the extras
    strip takes the whole call's server-owned keys, not only the service's own: a
    precondition's ``tenant`` under ``BUNDLE``, and the lookup's under a spread."""
    post = Post.objects.create(title="p")
    spec, kwargs = (
        (ServiceSpec(service=_data_seen, preconditions=[_gate], atomic=False), {})
        if hider == "precondition"
        else (
            ServiceSpec(
                service=_row_data_seen, instance_selector_spec=_TENANT_LOOKUP, atomic=False
            ),
            {"argument_binding": ArgumentBinding.SPREAD_AUTHOR_WINS},
        )
    )

    result = dispatch(
        spec,
        params={"pk": post.pk, "title": "t", "tenant": "evil"},
        unknown_arguments=UnknownArguments.PASSTHROUGH,
        **kwargs,
    )

    assert result.value["title"] == "t"
    assert "tenant" not in result.value


class _TitleAndTenant(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField(required=False)


def _spread_tenant(*, data: Any, tenant: str = "service-default") -> str:
    return tenant


@pytest.mark.django_db(transaction=True)
@_CORES
def test_a_field_named_like_a_key_a_precondition_hides_is_not_spread(
    dispatch: Dispatch, gate_seen: list[str]
) -> None:
    """The caller's ``tenant`` is the serializer's to validate, and stays in ``data``,
    but a precondition hides the key, so the spread strip keeps it out of the pool
    the precondition and the service share, though the service names it plainly."""
    spec = ServiceSpec(
        service=_spread_tenant,
        input_serializer=_TitleAndTenant,
        preconditions=[_gate],
        atomic=False,
    )

    result = dispatch(
        spec,
        params={"title": "t", "tenant": "evil"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert result.value == "service-default"
    assert gate_seen == ["own"]
