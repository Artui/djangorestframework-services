"""``DispatchResult``: what a drfs caller sees change now that it is the kernel's.

The value is django-service-specs' and tested there. It carries what a dispatch
produced and what kind of thing that is; the HTTP status a view answers with is
the view's to compute, so the result no longer carries one.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from rest_framework_services.types.dispatch_result import DispatchResult


def test_there_is_no_status() -> None:
    assert [field.name for field in fields(DispatchResult)] == [
        "kind",
        "value",
        "service_result",
        "instance",
        "data",
        # The presentation context the kernel carries from 0.8.0, which a
        # transport presenting the value itself hands back to the presenter.
        "context",
    ]
    with pytest.raises(TypeError, match="status"):
        DispatchResult(kind="list", value=[], status=200)  # ty: ignore[unknown-argument]


def test_the_kind_comes_first_and_the_value_defaults_to_none() -> None:
    """A positional construction reads the kind first, and a result with
    nothing to carry, as ``not_found`` has not, needs no value."""
    assert DispatchResult("list", [1]).kind == "list"
    assert DispatchResult(kind="not_found").value is None
