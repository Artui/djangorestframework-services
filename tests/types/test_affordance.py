"""``Affordance`` — what the declaration accepts, and what it refuses at construction."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import BooleanField, Exists, ExpressionWrapper, F, OuterRef, Q, Value
from django.db.models.lookups import GreaterThan

from rest_framework_services import Affordance
from tests.testapp.models import Item, Post

# --- accepted shapes --------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        Q(published=False),
        ~Q(title=""),
        Q(views__gt=1) & Q(title="x"),
        Exists(Item.objects.filter(section=OuterRef("pk"))),
        GreaterThan(F("views"), 1),
        ExpressionWrapper(Q(published=True), output_field=BooleanField()),
    ],
    ids=["q", "negated-q", "combined-q", "exists", "lookup", "boolean-wrapper"],
)
def test_a_boolean_orm_expression_is_a_row_condition(when: Any) -> None:
    affordance = Affordance(code="c", reason="r", when=when)
    assert affordance.when is when


def test_a_callable_naming_only_ambient_names_is_accepted() -> None:
    def refunds_open(*, user: Any, request: Any) -> bool:
        return True

    assert Affordance(code="c", reason="r", when=refunds_open).when is refunds_open


def test_a_catch_all_callable_is_accepted() -> None:
    """``**kwargs`` names nothing, so the declaration has nothing to refuse; the
    dispatcher withholds the per-call names from it instead."""

    def anything(**kwargs: Any) -> bool:
        return True

    assert Affordance(code="c", reason="r", when=anything).when is anything


# --- code and reason ---------------------------------------------------------


@pytest.mark.parametrize("field_name", ["code", "reason"])
@pytest.mark.parametrize("bad", ["", None, 7], ids=["empty", "none", "not-a-string"])
def test_code_and_reason_must_be_non_empty_strings(field_name: str, bad: Any) -> None:
    fields: dict[str, Any] = {"code": "c", "reason": "r", "when": Q(published=False)}
    fields[field_name] = bad
    with pytest.raises(ImproperlyConfigured, match=rf"Affordance\.{field_name} must be"):
        Affordance(**fields)


# --- when --------------------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [F("title"), F("views") + 1, Value("draft")],
    ids=["field-reference", "arithmetic", "string-value"],
)
def test_a_non_boolean_expression_is_refused(when: Any) -> None:
    """``filter()`` would refuse it too, but only on the first request that runs it."""
    with pytest.raises(ImproperlyConfigured, match="not a boolean one"):
        Affordance(code="c", reason="r", when=when)


@pytest.mark.parametrize("when", [True, "published", 1], ids=["bool", "str", "int"])
def test_neither_an_expression_nor_a_callable_is_refused(when: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="must be an ORM boolean expression") as info:
        Affordance(code="c", reason="r", when=when)
    # Sized so the expression branch cannot be the one answering.
    assert "not a boolean one" not in str(info.value)


def test_a_callable_reading_the_row_is_refused_and_pointed_at_orm_expressions() -> None:
    """The load-bearing refusal: a Python predicate over the row is one query per row."""

    def not_shipped(*, instance: Post) -> bool:
        return instance.published

    with pytest.raises(ImproperlyConfigured, match="must be an ORM expression") as info:
        Affordance(code="order_shipped", reason="r", when=not_shipped)
    assert "'order_shipped'" in str(info.value)


@pytest.mark.parametrize("name", ["collection", "data", "serializer"])
def test_a_callable_reading_the_calls_target_or_input_is_refused(name: str) -> None:
    namespace: dict[str, Any] = {}
    exec(f"def when(*, user, {name}): return True", namespace)  # noqa: S102

    with pytest.raises(ImproperlyConfigured, match=f"reads '{name}'") as info:
        Affordance(code="c", reason="r", when=namespace["when"])
    # Its own message, not the row one.
    assert "must be an ORM expression" not in str(info.value)


def test_the_row_refusal_wins_when_a_callable_also_reads_input() -> None:
    def both(*, instance: Any, data: Any) -> bool:
        return True

    with pytest.raises(ImproperlyConfigured, match="must be an ORM expression"):
        Affordance(code="c", reason="r", when=both)
