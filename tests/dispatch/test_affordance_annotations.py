"""``SelectorSpec.affordances`` — each row's answer, computed inside the list query.

The claims under test are the ones the declaration exists for: the answer rides
in the one query and the one ``.annotate()`` call a list already makes, it agrees
with the check the mutation runs for every row, and declaring nothing adds
nothing.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import Count, Prefetch, Q, QuerySet
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ActionUnavailable,
    Affordance,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Author, Post, Tag
from tests.testapp.serializers import PostSerializer

UNPUBLISHED = Affordance(code="already_published", reason="Published.", when=Q(published=False))
TAGGED = Affordance(code="untagged", reason="Untagged.", when=Q(tags__isnull=False))
AUTHORED = Affordance(code="no_author", reason="No author.", when=Q(author__name__isnull=False))
NOT_X = Affordance(code="tagged_x", reason="Tagged x.", when=~Q(tags__name="x"))

PUBLISH = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED, TAGGED, AUTHORED, NOT_X])
ARCHIVE = ServiceSpec(service=lambda: None, affordances=[AUTHORED])

_CODES = {
    "publish": ["already_published", "untagged", "no_author", "tagged_x"],
    "archive": ["no_author"],
}


def _all_posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _listing(**fields: Any) -> SelectorSpec[Any, Any]:
    fields.setdefault("affordances", {"publish": PUBLISH, "archive": ARCHIVE})
    return SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, **fields)


def _world() -> list[Post]:
    """Six rows, one per way a condition can go, so no answer is the same by accident."""
    ann = Author.objects.create(name="Ann")
    x, y, z = (Tag.objects.create(name=name) for name in ("x", "y", "z"))

    def post(title: str, tags: tuple[Tag, ...] = (), **fields: Any) -> Post:
        created = Post.objects.create(title=title, **fields)
        created.tags.add(*tags)
        return created

    return [
        post("available", (y, z), author=ann),
        post("published", (y,), author=ann, published=True),
        post("untagged", author=ann),
        post("orphan", (y,)),
        post("tagged x among others", (y, x), author=ann),
        post("everything wrong", (x,), published=True),
    ]


def _first_unmet(row: Any, name: str) -> str | None:
    """The code the row's annotations say the call would refuse with, if any."""
    for code in _CODES[name]:
        if not getattr(row, f"affordance__{name}__{code}"):
            return code
    return None


def _refusal(spec: ServiceSpec[Any, Any, Any], row: Post) -> str | None:
    """The code the mutation's own check refuses ``row`` with, if any."""
    try:
        dispatch_spec(spec, user=None, params={}, instance=row)
    except ActionUnavailable as exc:
        return exc.code
    return None


# --- the answer ------------------------------------------------------------------


@pytest.mark.django_db
def test_each_row_carries_one_boolean_per_condition() -> None:
    _world()
    rows = list(dispatch_spec(_listing(), user=None, params={}).value)

    publish = {
        row.title: [getattr(row, f"affordance__publish__{code}") for code in _CODES["publish"]]
        for row in rows
    }
    assert publish == {
        "available": [True, True, True, True],
        "published": [False, True, True, True],
        "untagged": [True, False, True, True],
        "orphan": [True, True, False, True],
        "tagged x among others": [True, True, True, False],
        "everything wrong": [False, True, False, False],
    }
    assert [row.affordance__archive__no_author for row in rows] == [
        True,
        True,
        True,
        False,
        True,
        False,
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "prefetch",
    [None, [Prefetch("tags", queryset=Tag.objects.filter(name="nothing-matches"))]],
    ids=["plain", "filtered-prefetch-on-the-same-relation"],
)
def test_the_list_and_the_mutation_agree_about_every_row(prefetch: Any) -> None:
    """The row the list reports available is the row the call lets through, and a
    ``Prefetch`` that hides a relation's rows from a Python predicate changes
    neither side."""
    _world()
    rows = list(dispatch_spec(_listing(prefetch_related=prefetch), user=None, params={}).value)
    assert len(rows) == 6

    for name, spec in (("publish", PUBLISH), ("archive", ARCHIVE)):
        listed = {row.title: _first_unmet(row, name) for row in rows}
        enforced = {row.title: _refusal(spec, row) for row in rows}
        assert listed == enforced, name
    # And the answers are not all the same, so agreement is not vacuous.
    assert len({_first_unmet(row, "publish") for row in rows}) == 5


@pytest.mark.django_db
def test_a_retrieve_selector_carries_the_answer_on_its_one_row() -> None:
    world = _world()
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        affordances={"publish": PUBLISH},
    )

    row = dispatch_spec(spec, user=None, params={"pk": world[3].pk}).value
    assert _first_unmet(row, "publish") == "no_author"


@pytest.mark.django_db
def test_a_mutations_output_selector_carries_the_answer_on_the_written_row() -> None:
    post = _world()[0]

    def unpublish(*, instance: Post) -> Post:
        instance.published = True
        instance.save(update_fields=["published"])
        return instance

    spec = ServiceSpec(
        service=unpublish,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, result: Post.objects.filter(pk=result.pk),
            affordances={"publish": PUBLISH},
        ),
    )
    row = dispatch_spec(spec, user=None, params={}, instance=post).value
    assert _first_unmet(row, "publish") == "already_published"


# --- cost --------------------------------------------------------------------------


def _run(spec: SelectorSpec[Any, Any]) -> tuple[list[Post], int, list[list[str]]]:
    real = QuerySet.annotate
    calls: list[list[str]] = []

    def spy(self: QuerySet[Any], *args: Any, **kwargs: Any) -> QuerySet[Any]:
        calls.append(sorted(kwargs))
        return real(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "annotate", spy), CaptureQueriesContext(connection) as ctx:
        rows = list(dispatch_spec(spec, user=None, params={}).value)
    return rows, len(ctx.captured_queries), calls


@pytest.mark.django_db
def test_every_answer_rides_in_the_one_list_query_and_the_one_annotate_call() -> None:
    _world()
    declared = {"n_tags": Count("tags")}

    rows, queries, calls = _run(_listing(annotations=declared))

    assert queries == 1
    assert len(calls) == 1
    assert calls[0] == sorted(
        ["n_tags"]
        + [f"affordance__{name}__{code}" for name, codes in _CODES.items() for code in codes]
    )
    # The declared aggregate is not inflated by the conditions' relations, and no
    # row is duplicated by a condition over a multi-valued relation.
    assert [row.n_tags for row in rows] == [2, 1, 0, 1, 2, 1]


@pytest.mark.django_db
def test_declaring_nothing_adds_nothing_to_the_query() -> None:
    _world()

    rows, queries, calls = _run(_listing(affordances=None))

    assert queries == 1
    assert calls == []
    assert len(rows) == 6
    assert not [name for name in vars(rows[0]) if name.startswith("affordance__")]
    with CaptureQueriesContext(connection) as ctx:
        list(dispatch_spec(_listing(affordances=None), user=None, params={}).value)
    assert "EXISTS" not in ctx.captured_queries[0]["sql"].upper()


@pytest.mark.django_db
def test_a_spec_with_no_affordances_of_its_own_adds_no_annotation() -> None:
    _world()
    _rows, queries, calls = _run(_listing(affordances={"edit": ServiceSpec(service=lambda: None)}))
    assert queries == 1
    assert calls == []


# --- callable conditions ------------------------------------------------------------


@pytest.mark.django_db
def test_a_callable_condition_is_answered_once_per_dispatch_not_per_row() -> None:
    _world()
    seen: list[dict[str, Any]] = []

    def books_open(**kwargs: Any) -> bool:
        seen.append(kwargs)
        return False

    closed = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="books_closed", reason="Closed.", when=books_open)],
    )
    author = Author.objects.create(name="acting user stand-in")

    rows = list(
        dispatch_spec(
            _listing(affordances={"refund": closed}),
            user=author,
            # A selector spreads its params into the pool: a client argument sits
            # beside the seeds, and must not reach the condition.
            params={"books_are_open": True},
        ).value
    )

    assert len(seen) == 1
    assert set(seen[0]) == {"user", "request", "progress"}
    assert seen[0]["user"] == author
    assert [row.affordance__refund__books_closed for row in rows] == [False] * 6


@pytest.mark.django_db
def test_a_callable_that_returns_nothing_projects_as_unavailable() -> None:
    """Read for truth, as the call reads it: a NULL here would render as neither."""
    _world()
    silent = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="c", reason="r", when=lambda: None)],
    )

    rows = list(dispatch_spec(_listing(affordances={"x": silent}), user=None, params={}).value)
    assert {row.affordance__x__c for row in rows} == {False}


@pytest.mark.django_db
def test_a_registered_pool_seed_reaches_a_projected_condition() -> None:
    _world()
    seeds = DEFAULT_POOL_SEEDS.extend(clock=lambda: 29)
    closed = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="c", reason="r", when=lambda *, clock: clock < 28)],
    )

    result = dispatch_spec(
        _listing(affordances={"refund": closed}), user=None, params={}, pool_seeds=seeds
    )
    assert {row.affordance__refund__c for row in result.value} == {False}


# --- refusals -------------------------------------------------------------------------


def test_affordances_without_a_selector_are_refused_at_as_view() -> None:
    class _View(SelectorListView):
        queryset = Post.objects.all()
        spec = SelectorSpec(kind=SelectorKind.LIST, affordances={"publish": PUBLISH})

    with pytest.raises(ImproperlyConfigured, match="affordances are set but `selector` is not"):
        _View.as_view()


@pytest.mark.parametrize("field", ["instance_selector_spec", "collection_selector_spec"])
def test_affordances_on_a_target_lookup_are_refused_at_as_view(field: str) -> None:
    kind = SelectorKind.RETRIEVE if field == "instance_selector_spec" else SelectorKind.LIST
    nested = SelectorSpec(kind=kind, selector=_all_posts, affordances={"publish": PUBLISH})

    class _View(ServiceUpdateView):
        spec = ServiceSpec(service=lambda **_: None, **{field: nested})

    with pytest.raises(ImproperlyConfigured, match="on a target lookup is never rendered") as info:
        _View.as_view()
    assert "is never invoked" not in str(info.value)


def test_affordances_on_an_output_selector_are_accepted_at_as_view() -> None:
    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=lambda **_: None,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_all_posts, affordances={"publish": PUBLISH}
            ),
        )

    _View.as_view()


@pytest.mark.django_db
def test_a_list_view_still_serves_its_rows() -> None:
    _world()

    class _View(SelectorListView):
        spec = _listing()

        def get_serializer_class(self) -> Any:
            return PostSerializer

    response = _View.as_view()(APIRequestFactory().get("/"))
    assert response.status_code == 200
    assert len(response.data) == 6


# --- async ------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_async_the_list_and_the_mutation_agree_and_a_querying_condition_runs_off_the_loop() -> (
    None
):
    await sync_to_async(_world, thread_sensitive=True)()

    def no_more_than_ten_posts() -> bool:
        return Post.objects.count() <= 10

    capped = ServiceSpec(
        service=lambda: None,
        affordances=[
            UNPUBLISHED,
            Affordance(code="too_many", reason="r", when=no_more_than_ten_posts),
        ],
    )
    result = await adispatch_spec(_listing(affordances={"publish": capped}), user=None, params={})
    rows = await sync_to_async(list, thread_sensitive=True)(result.value)

    for row in rows:
        try:
            await adispatch_spec(capped, user=None, params={}, instance=row)
            enforced = None
        except ActionUnavailable as exc:
            enforced = exc.code
        listed = next(
            (
                code
                for code in ("already_published", "too_many")
                if not getattr(row, f"affordance__publish__{code}")
            ),
            None,
        )
        assert listed == enforced, row.title


@pytest.mark.django_db(transaction=True)
async def test_async_a_registered_pool_seed_reaches_a_projected_condition() -> None:
    await sync_to_async(_world, thread_sensitive=True)()
    seeds = DEFAULT_POOL_SEEDS.extend(clock=lambda: 29)
    closed = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="c", reason="r", when=lambda *, clock: clock < 28)],
    )

    result = await adispatch_spec(
        _listing(affordances={"refund": closed}), user=None, params={}, pool_seeds=seeds
    )
    rows = await sync_to_async(list, thread_sensitive=True)(result.value)
    assert {row.affordance__refund__c for row in rows} == {False}
