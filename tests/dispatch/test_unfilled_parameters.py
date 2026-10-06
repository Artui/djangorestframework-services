"""A dispatched callable's parameter that nothing filled is refused, not crashed into.

A parameter with no default, absent from the assembled pool, used to reach the
callable and raise ``TypeError``: a declared optional read the caller did not
send, a ``kwargs=`` provider that declined it with ``UNSET``, or no provider at
all. Every transport built on ``dispatch_spec`` passed that crash on. An
``InputRequired`` key in the same position was already refused with
``ServiceValidationError``; these tests pin the unmarked parameter to the same
refusal, in the same shape, and pin what is *not* a missing argument: a reserved
seed, a defaulted parameter, ``**kwargs``, a positional-only parameter, and a
parameter of an affordance condition, none of which a caller can fill.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unset import UNSET, UnsetType
from rest_framework_services.viewsets.service_viewset import ServiceViewSet

# A service runs atomically, and the async path opens its transaction off the loop.
pytestmark = pytest.mark.django_db(transaction=True)


class _Scope(TypedDict):
    tenant: str | UnsetType


def _declining_scope() -> _Scope:
    # Declines, as a provider does off HTTP where there is no tenant to read.
    return {"tenant": UNSET}


def _plain(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _archive(*, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


_TENANT_MISSING = {"non_field_errors": ["Missing required argument(s): 'tenant'."]}


def _selector(**kwargs: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_plain, **kwargs)


def _service(**kwargs: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=_archive, **kwargs)


_UNFILLED = [
    pytest.param(_selector(kwargs=_declining_scope), {"pk": 1}, id="selector-declining-provider"),
    pytest.param(_selector(), {"pk": 1}, id="selector-no-provider"),
    pytest.param(_service(kwargs=_declining_scope), {}, id="service-declining-provider"),
    pytest.param(_service(), {}, id="service-no-provider"),
]


@pytest.mark.parametrize(("spec", "params"), _UNFILLED)
def test_an_unfilled_parameter_is_refused_naming_it(spec: Any, params: dict[str, Any]) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params=params)
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize(("spec", "params"), _UNFILLED)
async def test_async_dispatch_refuses_an_unfilled_parameter_naming_it(
    spec: Any, params: dict[str, Any]
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params=params)
    assert excinfo.value.detail == _TENANT_MISSING


def _lookup(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _precondition(*, tenant: str) -> None: ...


_EVERY_CALLABLE = [
    pytest.param(
        ServiceSpec(
            service=_archive,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        ),
        id="target-lookup",
    ),
    pytest.param(
        ServiceSpec(service=lambda **_: None, preconditions=[_precondition]),
        id="precondition",
    ),
]


@pytest.mark.parametrize("spec", _EVERY_CALLABLE)
def test_every_callable_dispatch_resolves_is_refused_the_same_way(spec: Any) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize("spec", _EVERY_CALLABLE)
async def test_async_every_callable_dispatch_resolves_is_refused_the_same_way(
    spec: Any,
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


def test_the_caller_fills_what_the_provider_declined() -> None:
    # The decline is what lets the caller's value through; refusing it would undo
    # the reason a provider may decline at all.
    result = dispatch_spec(
        _selector(kwargs=_declining_scope), user=None, params={"pk": 1, "tenant": "acme"}
    )
    assert result.value == {"pk": 1, "tenant": "acme"}


def test_one_message_lists_every_missing_name_sorted_and_once() -> None:
    # ``marked`` is both InputRequired and default-less, so both checks find it;
    # it is named once. The order is the sort, not the signature's.
    def select(
        *, zeta: int, marked: Annotated[int, InputRequired], alpha: int, pk: int
    ) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == {
        "non_field_errors": ["Missing required argument(s): 'alpha', 'marked', 'zeta'."]
    }


def test_a_missing_reserved_seed_is_not_reported_as_an_argument() -> None:
    # ``data`` is stripped from caller input, so naming it would ask the caller for
    # a value dispatch would throw away. Only the name a caller can send is listed.
    def select(*, data: Any, tenant: str) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={})
    assert excinfo.value.detail == _TENANT_MISSING


def test_a_reserved_seed_alone_still_fails_as_the_author_error_it_is() -> None:
    # Requiring a seed is the author's error, so dispatch leaves it to the callable
    # rather than turn it into a caller's validation error.
    def archive(*, instance: Any) -> None: ...

    with pytest.raises(TypeError, match="instance"):
        dispatch_spec(ServiceSpec(service=archive), user=None, params={"instance": 1})


def test_a_defaulted_parameter_is_never_missing() -> None:
    def select(*, pk: int, tenant: str = "default") -> dict[str, Any]:
        return {"pk": pk, "tenant": tenant}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    assert dispatch_spec(spec, user=None, params={"pk": 1}).value == {
        "pk": 1,
        "tenant": "default",
    }


def test_var_keyword_is_never_missing() -> None:
    def select(*args: Any, **kwargs: Any) -> list[str]:
        return sorted(kwargs)

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=select)
    assert "tenant" not in dispatch_spec(spec, user=None, params={}).value


def test_a_positional_only_parameter_is_not_reported() -> None:
    # Dispatch passes keywords only, so no caller value could ever fill it, and
    # naming it would ask for one.
    def select(tenant: str, /) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=select)
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={})


def test_an_affordance_condition_is_not_asked_of_the_caller() -> None:
    # A condition sees only the seeds, never client input, so a caller sending
    # ``tenant`` cannot fill it. Refusing would name an argument the caller has
    # already sent; the author's condition fails as the configuration error it is.
    def when(*, tenant: str) -> bool:
        return True

    spec = ServiceSpec(
        service=_archive, affordances=[Affordance(code="open", reason="Closed.", when=when)]
    )
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={"tenant": "acme"})


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()


def _create_plain(*, data: Any, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


def _create_marked(*, data: Any, tenant: Annotated[str, InputRequired]) -> dict[str, Any]:
    return {"tenant": tenant}


@pytest.mark.parametrize("create", [_create_plain, _create_marked], ids=["plain", "marked"])
def test_over_http_a_hook_that_leaves_a_parameter_out_is_refused_too(create: Any) -> None:
    # HTTP views dispatch through the same core. ``as_view()`` refuses a parameter
    # nothing could feed; with a hook declared it cannot tell, so a hook that leaves
    # the parameter out reaches dispatch, which answers the validation error rather
    # than a server error. The marked case is the ``InputRequired`` check, which
    # has always run here too.
    viewset = type(
        "_VS",
        (ServiceViewSet,),
        {
            "queryset": User.objects.all(),
            "action_specs": {"create": ServiceSpec(service=create, input_serializer=_TitleInput)},
            "get_service_kwargs": lambda self: {},
        },
    )
    response = viewset.as_view({"post": "create"})(
        APIRequestFactory().post("/x/", {"title": "t"}, format="json")
    )
    assert response.status_code == 400
    assert response.data == _TENANT_MISSING
