"""A missing required argument is refused as ``InvalidArguments``, never as a service's refusal.

It is DRF's ``ValidationError`` and the kernel's ``InvalidArguments`` at once,
so an ``except`` written against either catches it, and it is neither a
``ServiceValidationError`` nor a ``ServiceError``: a business rule a service
states is a different refusal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ArgumentBinding,
    InputRequired,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    adispatch_spec,
    dispatch_spec,
)


def _tag(*, tenant: str) -> dict[str, str]:
    return {"tenant": tenant}


_SPEC = ServiceSpec(service=_tag, atomic=False, permission_classes=[])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "call", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
def test_a_missing_argument_is_caught_as_drf_and_the_kernel_catch_it(
    call: Callable[..., Any],
) -> None:
    for caught in (ValidationError, KernelInvalidArguments):
        with pytest.raises(caught) as raised:
            call(
                _SPEC,
                user=None,
                params={},
                argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            )

        assert not isinstance(raised.value, ServiceValidationError | ServiceError)
        assert [str(error) for error in raised.value.detail["tenant"]] == [
            "This field is required."
        ]


def _unfilled(*, tenant: str) -> list[Any]:
    return []


def _marked(*, tenant: Annotated[str, InputRequired] = "") -> list[Any]:
    return []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "call", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
@pytest.mark.parametrize(
    "selector", [_unfilled, _marked], ids=["no-default", "marked-input-required"]
)
def test_a_selectors_missing_parameter_is_caught_as_drf_and_the_kernel_catch_it(
    call: Callable[..., Any], selector: Callable[..., Any]
) -> None:
    # 0.57.0 raised these two as ServiceValidationError, under non_field_errors;
    # the upgrade note in the changelog names both.
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector, permission_classes=[])
    for caught in (ValidationError, KernelInvalidArguments):
        with pytest.raises(caught) as raised:
            call(spec, user=None, params={})

        assert not isinstance(raised.value, ServiceValidationError | ServiceError)
        assert [str(error) for error in raised.value.detail["tenant"]] == [
            "This field is required."
        ]


# --- under BUNDLE a selector's own parameters are no caller input ------------------


@pytest.mark.django_db
def test_under_bundle_a_selectors_parameter_with_no_default_is_the_selectors_type_error() -> None:
    """The scope of the upgrade note: ``AUTO`` and ``SPREAD_AUTHOR_WINS`` refuse it as
    ``InvalidArguments``, and ``BUNDLE`` leaves it to the call, as on 0.57.0."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_unfilled, permission_classes=[])

    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={}, argument_binding=ArgumentBinding.BUNDLE)


@pytest.mark.django_db
def test_under_bundle_a_selectors_input_required_marker_is_not_enforced() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_marked, permission_classes=[])

    result = dispatch_spec(spec, user=None, params={}, argument_binding=ArgumentBinding.BUNDLE)

    assert result.value == []


def _any_instance(*, instance: Any) -> str:
    return "ran"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selector", [_unfilled, _marked], ids=["no-default", "marked-input-required"]
)
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=lambda binding: binding.name,
)
def test_a_target_lookups_missing_parameter_is_refused_under_every_binding(
    selector: Callable[..., Any], binding: ArgumentBinding
) -> None:
    spec = ServiceSpec(
        service=_any_instance,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        atomic=False,
        permission_classes=[],
    )

    with pytest.raises(ValidationError) as raised:
        dispatch_spec(spec, user=None, params={}, argument_binding=binding)

    assert [str(error) for error in raised.value.detail["tenant"]] == ["This field is required."]
