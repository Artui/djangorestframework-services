"""A missing required argument is refused as ``InvalidArguments``, never as a service's refusal.

It is DRF's ``ValidationError`` and the kernel's ``InvalidArguments`` at once,
so an ``except`` written against either catches it, and it is neither a
``ServiceValidationError`` nor a ``ServiceError``: a business rule a service
states is a different refusal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ArgumentBinding,
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
