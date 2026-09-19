"""``operation_affordances`` — what a transport checks before asking anything.

A transport reads this to skip building a pool or taking a thread hop for an
operation with nothing to ask at list time, so an answer that is empty when it
should not be hides an operation's conditions, and one that is not empty when it
should be costs every listed operation a hop. What ``unmet_operation_affordance``
does with the answer is covered in ``tests/dispatch/test_unmet_operation_affordance.py``.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from rest_framework_services import (
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    operation_affordances,
)
from tests.testapp.models import Post

UNPUBLISHED = Affordance(code="already_published", reason="Published.", when=Q(published=False))
BOOKS_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
FROZEN = Affordance(code="account_frozen", reason="The account is frozen.", when=lambda: True)
MONTH_END = Affordance(code="month_end", reason="It is month-end.", when=lambda: True)


def _service(**_: Any) -> None: ...


def _posts() -> QuerySet[Post]:
    return Post.objects.all()


def test_declaration_order_is_kept() -> None:
    """Three, not two: a pair can only be forwards or reversed, and a sort by code
    or by anything else could land on either by chance."""
    spec = ServiceSpec(service=_service, affordances=[MONTH_END, BOOKS_CLOSED, FROZEN])

    assert operation_affordances(spec) == (MONTH_END, BOOKS_CLOSED, FROZEN)


def test_a_row_condition_is_left_out_and_the_rest_keep_their_order() -> None:
    spec = ServiceSpec(service=_service, affordances=[BOOKS_CLOSED, UNPUBLISHED, FROZEN])

    assert operation_affordances(spec) == (BOOKS_CLOSED, FROZEN)


def test_a_spec_with_only_row_conditions_answers_empty() -> None:
    spec = ServiceSpec(service=_service, affordances=[UNPUBLISHED])

    assert operation_affordances(spec) == ()


def test_a_selector_answers_empty_whatever_it_projects() -> None:
    """Its mapping holds other operations, whose callable conditions are not its own."""
    cancel = ServiceSpec(service=_service, affordances=[BOOKS_CLOSED, UNPUBLISHED])
    selector = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, affordances={"cancel": cancel})

    assert operation_affordances(selector) == ()


def test_a_spec_declaring_none_answers_empty() -> None:
    assert operation_affordances(ServiceSpec(service=_service, affordances=None)) == ()
    assert operation_affordances(ServiceSpec(service=_service, affordances=[])) == ()
