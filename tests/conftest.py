"""Top-level test configuration and shared fixtures."""

from __future__ import annotations

import pytest

from rest_framework_services.mutations import utils as mutation_utils


@pytest.fixture
def rows_keyed_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a relation write's row errors to DRF's index-keyed shape.

    Which shape a collection's row error takes follows the installed DRF, and CI
    installs two: the locked release keys failing rows by index, the floor job's
    aligns them in a list. A test about *which row* an error names pins the shape,
    so it asserts one thing under both. The rule that picks the shape is tested
    unpinned, against DRF itself, in ``tests/mutations/test_relation_error_shape.py``.
    """
    monkeypatch.setattr(mutation_utils, "_keys_rows_by_index", lambda: True)
