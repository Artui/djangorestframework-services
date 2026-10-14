"""``RegisteredSpec``: what a drfs caller sees change now that it is the kernel's.

The entry is django-service-specs' and tested there. Its ``agent_contract`` is
no longer something passed in: it is read off the registered object, so the
declaration lives on the spec and the entry only asks.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.types.registered_spec import RegisteredSpec


class _DeclaresAContract:
    """Stands in for a spec that builds its own contract from its declarations."""

    def agent_contract(self) -> Any:
        return "the spec's own contract"


def test_the_contract_is_not_passed_to_the_entry() -> None:
    with pytest.raises(TypeError, match="agent_contract"):
        RegisteredSpec(name="x", spec=_DeclaresAContract(), agent_contract=None)  # ty: ignore[unknown-argument]


def test_the_entry_answers_the_contract_its_spec_declares() -> None:
    entry = RegisteredSpec(name="x", spec=_DeclaresAContract())  # ty: ignore[invalid-argument-type]

    assert entry.agent_contract == "the spec's own contract"
