"""A ``QueryParam`` a spec declares in ``reads`` is one of its parameters.

What ``QueryParam``'s docstring says of it there: the caller's value is checked
as declared, handed by name to the callable taking it, and never an unexpected
argument.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.exceptions import ValidationError

from rest_framework_services import ServiceSpec, dispatch_spec
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.unknown_arguments import UnknownArguments


def _page(*, page: Any = None) -> Any:
    return page


_SPEC = ServiceSpec(service=_page, reads=[QueryParam("page", type="integer")], atomic=False)


def test_a_declared_query_param_is_no_unexpected_argument() -> None:
    result = dispatch_spec(
        _SPEC, user=None, params={"page": 2}, unknown_arguments=UnknownArguments.REJECT
    )

    assert result.value == 2


def test_a_declared_query_param_is_checked_as_its_type() -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_SPEC, user=None, params={"page": "two"})

    assert set(excinfo.value.detail) == {"page"}
