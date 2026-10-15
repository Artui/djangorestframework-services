"""An open spread service takes the caller's value for its preconditions' parameters, as on 0.57.0.

Under ``SPREAD_AUTHOR_WINS`` a service with no input serializer declares its
own signature as the caller's input, and a bare ``**kwargs`` declares every
name. So a precondition's parameter beside one is the caller's to send: the
value sent reaches the precondition, and one left out with no default is the
caller's ``400``. A service that closes its signature (named parameters alone,
or an ``Unpack``-ed ``TypedDict``) declares no such name, so there the
parameter stays the author's ``TypeError``, as it does under ``AUTO`` and
``BUNDLE``, for a name the server owns, and for a read of the target lookup,
which the service takes only by name.
"""

from __future__ import annotations

import functools
from typing import Annotated, Any, TypedDict

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError
from typing_extensions import Unpack

from rest_framework_services import (
    ArgumentBinding,
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

SPREAD = ArgumentBinding.SPREAD_AUTHOR_WINS


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_POLICIES = pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT], ids=["ignore", "reject"]
)


class _WrongTenant(Exception):
    pass


def _tenant_is_acme(*, tenant: str) -> None:
    if tenant != "acme":
        raise _WrongTenant(tenant)


def _open(**_: Any) -> str:
    return "ran"


async def _aopen(**_: Any) -> str:
    return "ran"


_OPEN = pytest.mark.parametrize("service", [_open, _aopen], ids=["def", "async-def"])


@_CORES
@_POLICIES
@_OPEN
def test_the_caller_s_value_reaches_the_precondition(
    dispatch: Any, policy: UnknownArguments, service: Any
) -> None:
    spec = ServiceSpec(service=service, preconditions=[_tenant_is_acme])
    result = dispatch(
        spec, params={"tenant": "acme"}, argument_binding=SPREAD, unknown_arguments=policy
    )
    assert result.value == "ran"
    with pytest.raises(_WrongTenant, match="other"):
        dispatch(
            spec, params={"tenant": "other"}, argument_binding=SPREAD, unknown_arguments=policy
        )


@_CORES
@_OPEN
def test_a_precondition_parameter_left_out_is_the_caller_s_400(dispatch: Any, service: Any) -> None:
    spec = ServiceSpec(service=service, preconditions=[_tenant_is_acme])
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={}, argument_binding=SPREAD)
    assert excinfo.value.detail == {"tenant": ["This field is required."]}


def _tenant_defaults_to_acme(*, tenant: str = "acme") -> None:
    if tenant != "acme":
        raise _WrongTenant(tenant)


@_CORES
def test_a_precondition_s_default_answers_until_the_caller_sends_a_value(dispatch: Any) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_tenant_defaults_to_acme])
    assert dispatch(spec, params={}, argument_binding=SPREAD).value == "ran"
    with pytest.raises(_WrongTenant, match="other"):
        dispatch(spec, params={"tenant": "other"}, argument_binding=SPREAD)


def _injecting(fn: Any) -> Any:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("tenant", "acme")
        return fn(*args, **kwargs)

    return wrapper


@_CORES
def test_a_precondition_wrapper_filling_the_parameter_itself_runs(dispatch: Any) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_injecting(_tenant_is_acme)])
    assert dispatch(spec, params={}, argument_binding=SPREAD).value == "ran"


def _closed(*, title: str = "") -> str:
    return "ran"


class _Titled(TypedDict, total=False):
    title: str


def _unpacked(**changes: Unpack[_Titled]) -> str:
    return "ran"


@_CORES
@pytest.mark.parametrize("service", [_closed, _unpacked], ids=["named", "typed-dict"])
def test_a_closed_service_declares_no_precondition_parameter(dispatch: Any, service: Any) -> None:
    spec = ServiceSpec(service=service, preconditions=[_tenant_is_acme])
    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={"tenant": "acme"}, argument_binding=SPREAD)


@_CORES
@pytest.mark.parametrize("binding", [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE])
def test_without_a_spreading_binding_the_parameter_is_the_author_s(
    dispatch: Any, binding: ArgumentBinding
) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_tenant_is_acme])
    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={"tenant": "acme"}, argument_binding=binding)


def _hidden_secret(*, secret: Annotated[str, NotClientInput]) -> None:
    raise AssertionError(f"saw {secret!r}")


@_CORES
def test_a_name_the_server_owns_never_takes_the_caller_s_value(dispatch: Any) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_hidden_secret])
    for params in ({}, {"secret": "evil"}):
        with pytest.raises(TypeError, match="secret"):
            dispatch(spec, params=params, argument_binding=SPREAD)


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _pk_is_999(*, pk: Any) -> None:
    raise AssertionError(f"saw {pk!r}")


@_CORES
def test_a_read_of_the_target_lookup_stays_the_lookup_s(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=lambda *, instance, **_: "ran",
        preconditions=[_pk_is_999],
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    )
    context = build_offline_context(None, kwargs={"pk": post.pk})
    for params in ({}, {"pk": "999"}):
        with pytest.raises(TypeError, match="_pk_is_999"):
            dispatch(
                spec,
                params=params,
                request=context.request,
                view=context.view,
                argument_binding=SPREAD,
            )


def _sees_user(*, user: Any) -> None:
    if user == "evil":
        raise AssertionError("the caller's user")


@_CORES
def test_a_seed_s_name_is_the_seed_s(dispatch: Any) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_sees_user])
    assert dispatch(spec, params={"user": "evil"}, argument_binding=SPREAD).value == "ran"


def _also_tenant(*, tenant: str) -> None:
    if tenant != "acme":
        raise _WrongTenant(tenant)


@_CORES
def test_two_preconditions_naming_one_parameter_share_the_caller_s_value(dispatch: Any) -> None:
    spec = ServiceSpec(service=_open, preconditions=[_tenant_is_acme, _also_tenant])
    assert dispatch(spec, params={"tenant": "acme"}, argument_binding=SPREAD).value == "ran"


def _named_and_open(*, tenant: str = "unset", **_: Any) -> str:
    return tenant


@_CORES
def test_a_parameter_the_service_names_too_is_the_service_s_declaration(dispatch: Any) -> None:
    spec = ServiceSpec(service=_named_and_open, preconditions=[_tenant_is_acme])
    assert dispatch(spec, params={"tenant": "acme"}, argument_binding=SPREAD).value == "acme"


def _unresolvable(**changes: Unpack[_NotDefinedAnywhere]) -> str:  # noqa: F821
    return "ran"


@_CORES
def test_an_annotation_that_cannot_be_resolved_reads_as_open(dispatch: Any) -> None:
    """As ``UnknownArguments.IGNORE`` reads it; ``REJECT`` refuses to guess."""
    spec = ServiceSpec(service=_unresolvable, preconditions=[_tenant_is_acme])
    assert dispatch(spec, params={"tenant": "acme"}, argument_binding=SPREAD).value == "ran"
