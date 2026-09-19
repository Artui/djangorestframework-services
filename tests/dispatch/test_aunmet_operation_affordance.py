"""``aunmet_operation_affordance`` — the async twin of ``unmet_operation_affordance``.

The twins' signatures are held equal in ``tests/test_sync_async_parity.py``. This
pins that the awaited form gives the sync form's answers, that a condition which
queries runs off the event loop, and what the executor hop costs: a transport asks
this for every operation it lists, so a spec with nothing to ask at list time must
not pay for one.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.db.models import Q, QuerySet

from rest_framework_services import (
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    aunmet_operation_affordance,
    base_pool,
    unmet_operation_affordance,
)
from tests.testapp.models import Post

# The submodule, not the function the package re-exports under the same name: the
# executor hop is looked up in this module's namespace, so that is where it is counted.
aunmet_module = importlib.import_module(
    "rest_framework_services.dispatch.aunmet_operation_affordance"
)

ROW = Affordance(code="already_published", reason="Published.", when=Q(published=False))
OPEN = Affordance(code="open", reason="Closed.", when=lambda: True)
BOOKS_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
FROZEN = Affordance(code="account_frozen", reason="The account is frozen.", when=lambda: False)


def _service(**_: Any) -> None: ...


def _posts() -> QuerySet[Post]:
    return Post.objects.all()


def _pool() -> dict[str, Any]:
    return base_pool(user=None, request=None)


@pytest.fixture
def hops(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The name of every function sent to the executor through this module."""
    sent: list[str] = []
    real = aunmet_module.arun_off_loop

    async def counting(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        sent.append(fn.__name__)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(aunmet_module, "arun_off_loop", counting)
    return sent


@pytest.mark.parametrize(
    "affordances",
    [None, [ROW], [OPEN], [BOOKS_CLOSED], [ROW, OPEN, FROZEN, BOOKS_CLOSED]],
    ids=["undeclared", "row-only", "met", "unmet", "mixed"],
)
async def test_the_awaited_answer_is_the_sync_answer(affordances: list[Affordance] | None) -> None:
    spec = ServiceSpec(service=_service, affordances=affordances)

    assert await aunmet_operation_affordance(spec, _pool()) is unmet_operation_affordance(
        spec, _pool()
    )


async def test_the_first_unmet_condition_is_the_answer() -> None:
    spec = ServiceSpec(service=_service, affordances=[ROW, OPEN, FROZEN, BOOKS_CLOSED])

    answer = await aunmet_operation_affordance(spec, _pool())

    assert answer is FROZEN
    assert answer.code != BOOKS_CLOSED.code


@pytest.mark.parametrize(
    "spec",
    [
        ServiceSpec(service=_service),
        ServiceSpec(service=_service, affordances=[]),
        ServiceSpec(service=_service, affordances=[ROW]),
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts,
            affordances={"cancel": ServiceSpec(service=_service, affordances=[BOOKS_CLOSED])},
        ),
    ],
    ids=["undeclared", "empty", "row-conditions-only", "selector"],
)
async def test_nothing_to_ask_at_list_time_costs_no_executor_hop(
    spec: Any, hops: list[str]
) -> None:
    assert await aunmet_operation_affordance(spec, _pool()) is None
    assert hops == []


async def test_a_condition_to_ask_is_asked_in_the_executor(hops: list[str]) -> None:
    spec = ServiceSpec(service=_service, affordances=[ROW, BOOKS_CLOSED])

    assert await aunmet_operation_affordance(spec, _pool()) is BOOKS_CLOSED
    assert hops == ["unmet_operation_affordance"]


@pytest.mark.django_db(transaction=True)
async def test_a_condition_that_queries_runs_off_the_loop() -> None:
    """On the loop, the ORM raises ``SynchronousOnlyOperation`` before answering."""

    def no_drafts() -> bool:
        return not Post.objects.filter(published=False).exists()

    spec = ServiceSpec(
        service=_service,
        affordances=[Affordance(code="drafts_pending", reason="Drafts pending.", when=no_drafts)],
    )

    assert await aunmet_operation_affordance(spec, _pool()) is None
    await Post.objects.acreate(title="draft")
    answer = await aunmet_operation_affordance(spec, _pool())
    assert answer is not None
    assert answer.code == "drafts_pending"
