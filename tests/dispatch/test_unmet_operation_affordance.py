"""``unmet_operation_affordance`` — whether a transport should offer an operation at all.

Asked with no instance and no call being attempted: by a tool list, or an agent's
toolset for its next step. What belongs here is what makes that answer safe to
build a list from -- it never touches a row, it names the same condition a call
would be refused with, it sees exactly the names a call's condition sees, and it
never mistakes a selector's projection of other operations for conditions of its
own. How a call enforces the same declarations is covered in
``tests/dispatch/test_affordances.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.db.models import Q, QuerySet
from django.test.utils import CaptureQueriesContext

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ActionUnavailable,
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    base_pool,
    enforce_affordances,
    unmet_operation_affordance,
)
from tests.testapp.models import Post

# Unmet for every row there is: a persisted row always has a primary key. Were it
# evaluated at all, the answer for this operation would be "not available".
NO_ROW_QUALIFIES = Affordance(
    code="no_row_qualifies", reason="No row qualifies.", when=Q(pk__isnull=True)
)
OPEN = Affordance(code="open", reason="Closed.", when=lambda: True)
BOOKS_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
FROZEN = Affordance(code="account_frozen", reason="The account is frozen.", when=lambda: False)


def _service(**_: Any) -> None: ...


def _spec(*affordances: Affordance) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=_service, affordances=list(affordances))


def _pool() -> dict[str, Any]:
    return base_pool(user=None, request=None)


# --- what is asked ------------------------------------------------------------


@pytest.mark.django_db
def test_a_row_condition_is_skipped_without_a_query_or_an_instance() -> None:
    """Declared first and unmet for every row, and still not the answer.

    At list time there is no row to ask it about, so it is not asked: no query, and
    no instance needed, where the call-time check refuses to run it without one.
    """
    spec = _spec(NO_ROW_QUALIFIES, OPEN)

    with CaptureQueriesContext(connection) as ctx:
        answer = unmet_operation_affordance(spec, _pool())

    assert answer is None
    assert ctx.captured_queries == []


@pytest.mark.parametrize(
    "affordances",
    [None, [], [NO_ROW_QUALIFIES], [OPEN]],
    ids=["undeclared", "empty", "row-conditions-only", "all-met"],
)
def test_nothing_unmet_to_ask_about_answers_none(affordances: list[Affordance] | None) -> None:
    spec = ServiceSpec(service=_service, affordances=affordances)

    assert unmet_operation_affordance(spec, _pool()) is None


def test_the_first_unmet_condition_in_declaration_order_is_the_answer() -> None:
    """The one a call would be refused with, and nothing past it is asked."""
    asked: list[str] = []

    def closed(code: str) -> Affordance:
        def when() -> bool:
            asked.append(code)
            return False

        return Affordance(code=code, reason="Closed.", when=when)

    first, second = closed("books_closed"), closed("account_frozen")

    answer = unmet_operation_affordance(_spec(OPEN, first, second), _pool())

    assert answer is first
    assert answer.code != second.code
    assert asked == ["books_closed"]


@pytest.mark.parametrize(
    ("returned", "unmet"),
    [(True, False), (1, False), (False, True), (None, True), (0, True)],
)
def test_a_callable_is_read_for_truth_and_nothing_returned_is_unmet(
    returned: Any, unmet: bool
) -> None:
    condition = Affordance(code="books_closed", reason="Closed.", when=lambda: returned)

    answer = unmet_operation_affordance(_spec(condition), _pool())

    assert (answer is condition) is unmet


def test_an_exception_from_a_condition_propagates() -> None:
    """A condition that cannot be answered is not a "no": the transport decides."""

    def broken() -> bool:
        raise RuntimeError("the clock service is down")

    spec = _spec(Affordance(code="books_closed", reason="Closed.", when=broken))

    with pytest.raises(RuntimeError, match="the clock service is down"):
        unmet_operation_affordance(spec, _pool())


# --- what a condition sees -----------------------------------------------------


def test_a_condition_sees_a_registered_seed_and_never_a_spread_entry() -> None:
    """The transport's pool, built as the call's would be, seen as the call sees it.

    ``month_end`` is spread in beside the seeds -- the shape of a client argument --
    and were it visible the caller would decide whether the operation is offered.
    """
    seeds = DEFAULT_POOL_SEEDS.extend(clock=lambda: "the 29th")
    seen: dict[str, Any] = {}

    def books_open(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    spec = _spec(Affordance(code="books_closed", reason="Closed.", when=books_open))
    pool = base_pool(user="alice", request=None, seeds=seeds, month_end=False)

    assert unmet_operation_affordance(spec, pool, reserved=seeds.reserved) is None

    assert seen["clock"] == "the 29th"
    assert seen["user"] == "alice"
    assert "month_end" not in seen
    assert set(seen) == {"user", "request", "progress", "clock"}


def test_a_registered_seed_is_withheld_unless_its_reservation_is_passed() -> None:
    """Why the docstring tells a transport to pass ``reserved=seeds.reserved``.

    Without it the seed is indistinguishable from a spread entry, so a condition
    reading it is asked without it here while the call answers it with it.
    """
    seeds = DEFAULT_POOL_SEEDS.extend(clock=lambda: "the 29th")
    seen: dict[str, Any] = {}

    def books_open(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    spec = _spec(Affordance(code="books_closed", reason="Closed.", when=books_open))

    unmet_operation_affordance(spec, base_pool(user=None, request=None, seeds=seeds))

    assert "clock" not in seen


def test_the_calls_own_names_never_reach_a_condition() -> None:
    """A transport that hands over a pool carrying a target or input by mistake
    still gets an answer that does not depend on attempting the call."""
    seen: dict[str, Any] = {}

    def anything(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    spec = _spec(Affordance(code="c", reason="r", when=anything))
    pool = base_pool(user=None, request=None, instance="row", data={"title": "x"})

    unmet_operation_affordance(spec, pool)

    assert set(seen) == {"user", "request", "progress"}


# --- a selector ------------------------------------------------------------------


def _posts() -> QuerySet[Post]:
    return Post.objects.all()


def test_a_selector_answers_none_without_asking_what_it_projects() -> None:
    """``SelectorSpec.affordances`` is other operations' availability, per row.

    Read as the selector's own conditions it would hide a list because some
    *other* operation is closed; the condition here fails the test if it is asked.
    """

    def never(**_: Any) -> bool:
        pytest.fail("a selector's projected condition was asked as its own")

    cancel = ServiceSpec(
        service=_service,
        affordances=[Affordance(code="closed", reason="Closed.", when=never), NO_ROW_QUALIFIES],
    )
    selector = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, affordances={"cancel": cancel})

    assert unmet_operation_affordance(selector, _pool()) is None


# --- agreement with the call ---------------------------------------------------------


@pytest.mark.parametrize(
    "affordances",
    [
        [OPEN],
        [BOOKS_CLOSED],
        [OPEN, BOOKS_CLOSED],
        [BOOKS_CLOSED, FROZEN],
        [FROZEN, BOOKS_CLOSED],
        [Affordance(code="returns_nothing", reason="Nothing.", when=lambda: None)],
    ],
    ids=["met", "unmet", "met-then-unmet", "two-unmet", "two-unmet-reversed", "returns-none"],
)
def test_the_answer_is_the_refusal_the_call_would_raise(affordances: list[Affordance]) -> None:
    """For operation-scope conditions only, the list and the call cannot disagree:
    offered means the call gets through, and withheld names the code it is refused
    with."""
    spec = _spec(*affordances)
    pool = _pool()

    answer = unmet_operation_affordance(spec, pool)

    if answer is None:
        assert enforce_affordances(spec, pool, instance=None) is None
    else:
        with pytest.raises(ActionUnavailable) as exc:
            enforce_affordances(spec, pool, instance=None)
        assert exc.value.code == answer.code
        assert exc.value.message == answer.reason
