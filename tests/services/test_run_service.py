"""Tests for ``run_service`` — sync dispatch with optional atomic wrapping."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from rest_framework_services.services.run_service import run_service
from tests.testapp.models import Author


@pytest.mark.django_db(transaction=True)
class TestRunServiceSync:
    def test_passes_kwargs(self) -> None:
        def fn(*, x: int, y: int) -> int:
            return x + y

        assert run_service(fn, {"x": 2, "y": 3}, atomic=False) == 5

    def test_no_atomic_no_transaction(self) -> None:
        captured: dict[str, bool] = {}

        def fn() -> None:
            captured["in_atomic"] = transaction.get_connection().in_atomic_block

        run_service(fn, {}, atomic=False)
        assert captured["in_atomic"] is False

    def test_atomic_wraps(self) -> None:
        captured: dict[str, bool] = {}

        def fn() -> None:
            captured["in_atomic"] = transaction.get_connection().in_atomic_block

        run_service(fn, {}, atomic=True)
        assert captured["in_atomic"] is True

    def test_atomic_rollback_on_exception(self) -> None:
        Author.objects.create(name="seed")

        def fn() -> None:
            Author.objects.create(name="will-rollback")
            raise IntegrityError("forced rollback")

        with pytest.raises(IntegrityError):
            run_service(fn, {}, atomic=True)

        assert Author.objects.count() == 1

    def test_returns_value(self) -> None:
        def fn() -> str:
            return "ok"

        assert run_service(fn, {}, atomic=True) == "ok"
