"""``SelectorSpec.affordances`` on a selector that returns rows rather than a ``QuerySet``.

A list of instances, a bare instance from a ``RETRIEVE`` selector, mappings, and
what is refused. The claims are the ones the ``QuerySet`` path makes: the same
answer under the same name for every row, agreement with the call's own check,
and a cost that is fixed rather than per row.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import Q, QuerySet
from django.test.utils import CaptureQueriesContext

from rest_framework_services import (
    ActionUnavailable,
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Author, Catalog, Post, PublishedPost, Section, Tag

UNPUBLISHED = Affordance(code="already_published", reason="Published.", when=Q(published=False))
TAGGED = Affordance(code="untagged", reason="Untagged.", when=Q(tags__isnull=False))
AUTHORED = Affordance(code="no_author", reason="No author.", when=Q(author__name__isnull=False))
NOT_X = Affordance(code="tagged_x", reason="Tagged x.", when=~Q(tags__name="x"))
OPEN = Affordance(code="books_closed", reason="Closed.", when=lambda: True)

PUBLISH = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED, TAGGED, AUTHORED, NOT_X])
ARCHIVE = ServiceSpec(service=lambda: None, affordances=[AUTHORED, OPEN])
ONLY_CALLABLE = ServiceSpec(service=lambda: None, affordances=[OPEN])

_CODES = {
    "publish": ["already_published", "untagged", "no_author", "tagged_x"],
    "archive": ["no_author", "books_closed"],
}


def _world() -> None:
    ann = Author.objects.create(name="Ann")
    x, y, z = (Tag.objects.create(name=name) for name in ("x", "y", "z"))

    def post(title: str, tags: tuple[Tag, ...] = (), **fields: Any) -> None:
        Post.objects.create(title=title, **fields).tags.add(*tags)

    post("available", (y, z), author=ann)
    post("published", (y,), author=ann, published=True)
    post("untagged", author=ann)
    post("orphan", (y,))
    post("tagged x among others", (y, x), author=ann)
    post("everything wrong", (x,), published=True)


def _posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _listing(selector: Any, **fields: Any) -> SelectorSpec[Any, Any]:
    fields.setdefault("affordances", {"publish": PUBLISH, "archive": ARCHIVE})
    return SelectorSpec(kind=SelectorKind.LIST, selector=selector, **fields)


def _first_unmet(row: Any, name: str) -> str | None:
    for code in _CODES[name]:
        flag = (
            row[f"affordance__{name}__{code}"]
            if isinstance(row, dict)
            else getattr(row, f"affordance__{name}__{code}")
        )
        if not flag:
            return code
    return None


def _refusal(spec: ServiceSpec[Any, Any, Any], row: Post) -> str | None:
    try:
        dispatch_spec(spec, user=None, params={}, instance=row)
    except ActionUnavailable as exc:
        return exc.code
    return None


def _queries(spec: SelectorSpec[Any, Any]) -> tuple[list[Any], int]:
    with CaptureQueriesContext(connection) as ctx:
        value = dispatch_spec(spec, user=None, params={}).value
    return value, len(ctx.captured_queries)


# --- a list of instances ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_list_agrees_with_the_queryset_and_with_the_call_about_every_row() -> None:
    _world()
    listed = dispatch_spec(_listing(lambda: list(_posts())), user=None, params={}).value
    queried = list(dispatch_spec(_listing(_posts), user=None, params={}).value)

    assert isinstance(listed, list)
    for name, spec in (("publish", PUBLISH), ("archive", ARCHIVE)):
        from_list = {row.title: _first_unmet(row, name) for row in listed}
        from_queryset = {row.title: _first_unmet(row, name) for row in queried}
        enforced = {row.title: _refusal(spec, row) for row in queried}
        assert from_list == from_queryset == enforced, name
    # Not vacuous: the rows disagree with each other.
    assert len({_first_unmet(row, "publish") for row in listed}) == 5


@pytest.mark.django_db
def test_a_list_of_instances_costs_one_query_whatever_the_row_and_condition_count() -> None:
    _world()
    _rows, baseline = _queries(_listing(lambda: list(_posts()), affordances=None))

    one_condition, with_one = _queries(
        _listing(
            lambda: list(_posts()),
            affordances={"publish": ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED])},
        )
    )
    every_condition, with_all = _queries(_listing(lambda: list(_posts())))

    assert len(one_condition) == len(every_condition) == 6
    assert with_one == baseline + 1
    assert with_all == baseline + 1


@pytest.mark.django_db
def test_declaring_nothing_on_a_list_costs_nothing_and_adds_nothing() -> None:
    _world()
    plain = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: list(_posts()))

    rows, queries = _queries(plain)
    _declared_none, queries_declared_none = _queries(
        _listing(lambda: list(_posts()), affordances=None)
    )

    assert queries == queries_declared_none == 1
    assert not [name for name in vars(rows[0]) if name.startswith("affordance__")]


@pytest.mark.django_db
def test_an_empty_list_spends_no_query() -> None:
    rows, queries = _queries(_listing(lambda: []))
    assert (rows, queries) == ([], 0)


@pytest.mark.django_db
def test_only_callable_conditions_on_a_list_spend_no_row_query() -> None:
    _world()
    _rows, baseline = _queries(_listing(lambda: list(_posts()), affordances=None))
    rows, queries = _queries(_listing(lambda: list(_posts()), affordances={"a": ONLY_CALLABLE}))

    assert queries == baseline
    assert {row.affordance__a__books_closed for row in rows} == {True}


@pytest.mark.django_db
def test_one_query_per_model_class_present_and_each_answered_against_its_own_table() -> None:
    """Pks collide across the two tables, so answering one class's rows against the
    other's table would read the wrong titles."""
    post = Post.objects.create(title="draft")
    other_post = Post.objects.create(title="not a draft")
    catalog = Catalog.objects.create(name="c")
    section = Section.objects.create(catalog=catalog, title="not a draft")
    other_section = Section.objects.create(catalog=catalog, title="draft")
    assert {post.pk, other_post.pk} == {section.pk, other_section.pk}
    titled = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="not_draft", reason="r", when=Q(title="draft"))],
    )

    def mixed() -> list[Any]:
        return [
            Post.objects.get(pk=post.pk),
            Post.objects.get(pk=other_post.pk),
            Section.objects.get(pk=section.pk),
            Section.objects.get(pk=other_section.pk),
        ]

    _rows, baseline = _queries(_listing(mixed, affordances=None))
    rows, queries = _queries(_listing(mixed, affordances={"edit": titled}))

    assert queries == baseline + 2
    assert [row.affordance__edit__not_draft for row in rows] == [True, False, False, True]


@pytest.mark.django_db
def test_a_generator_is_walked_once_and_the_list_flows_on() -> None:
    _world()

    def lazily() -> Iterator[Post]:
        yield from _posts()

    rows = dispatch_spec(_listing(lazily), user=None, params={}).value

    assert isinstance(rows, list)
    assert [_first_unmet(row, "publish") for row in rows][:2] == [None, "already_published"]


@pytest.mark.django_db
def test_a_row_deleted_after_the_selector_ran_reads_as_unavailable() -> None:
    _world()

    def then_delete() -> list[Post]:
        rows = list(_posts())
        Post.objects.filter(pk=rows[0].pk).delete()
        return rows

    rows = dispatch_spec(_listing(then_delete), user=None, params={}).value

    gone = rows[0]
    assert [getattr(gone, f"affordance__publish__{code}") for code in _CODES["publish"]] == [
        False,
        False,
        False,
        False,
    ]
    # Callable conditions are not about the row, and still answer.
    assert gone.affordance__archive__books_closed is True
    # The rows that still exist are answered normally.
    assert _first_unmet(rows[1], "publish") == "already_published"


@pytest.mark.django_db
def test_a_default_manager_that_hides_the_rows_does_not_decide() -> None:
    """The rows are in hand; a default manager filtering drafts must not make them
    read as gone."""
    PublishedPost(title="draft", published=False).save()
    always = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="c", reason="r", when=Q(pk__isnull=False))],
    )

    rows = dispatch_spec(
        _listing(lambda: list(PublishedPost._base_manager.all()), affordances={"a": always}),
        user=None,
        params={},
    ).value

    assert [row.affordance__a__c for row in rows] == [True]


@pytest.mark.django_db
def test_an_instance_with_no_primary_key_is_refused_by_name() -> None:
    spec = _listing(lambda: [Post(title="never saved")])
    with pytest.raises(
        ImproperlyConfigured, match=r"returned <Post: .*>, which has no primary key"
    ):
        dispatch_spec(spec, user=None, params={})


@pytest.mark.django_db
def test_an_instance_with_no_primary_key_is_fine_with_only_callable_conditions() -> None:
    """Sized so only the row-condition conjunct decides: nothing needs finding."""
    rows = dispatch_spec(
        _listing(lambda: [Post(title="never saved")], affordances={"a": ONLY_CALLABLE}),
        user=None,
        params={},
    ).value
    assert rows[0].affordance__a__books_closed is True


# --- a bare row from a RETRIEVE selector --------------------------------------------------


@pytest.mark.django_db
def test_a_retrieve_selector_returning_an_instance_carries_its_answers() -> None:
    _world()
    orphan = Post.objects.get(title="orphan")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.get(pk=pk),
        affordances={"publish": PUBLISH},
    )

    row = dispatch_spec(spec, user=None, params={"pk": orphan.pk}).value

    assert _first_unmet(row, "publish") == _refusal(PUBLISH, orphan) == "no_author"


@pytest.mark.django_db
def test_a_retrieve_selector_returning_nothing_passes_nothing_through() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: None,
        allow_none=True,
        affordances={"publish": PUBLISH},
    )
    result = dispatch_spec(spec, user=None, params={})
    assert (result.kind, result.value) == ("instance", None)


@pytest.mark.django_db
def test_a_retrieve_selector_returning_a_mapping_gets_a_new_one() -> None:
    returned = {"id": 7, "title": "computed"}
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda: returned, affordances={"a": ONLY_CALLABLE}
    )

    row = dispatch_spec(spec, user=None, params={}).value

    assert row == {"id": 7, "title": "computed", "affordance__a__books_closed": True}
    assert returned == {"id": 7, "title": "computed"}


# --- mappings --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_mapping_rows_get_new_mappings_with_callable_answers() -> None:
    returned = [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    spec = _listing(lambda: returned, affordances={"a": ONLY_CALLABLE})

    rows, queries = _queries(spec)

    assert rows == [
        {"id": 1, "title": "a", "affordance__a__books_closed": True},
        {"id": 2, "title": "b", "affordance__a__books_closed": True},
    ]
    assert returned == [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    assert queries == 0


@pytest.mark.django_db
def test_a_row_condition_on_mapping_rows_is_refused_with_the_way_out() -> None:
    spec = _listing(lambda: [{"id": 1}], affordances={"publish": PUBLISH})

    with pytest.raises(ImproperlyConfigured) as info:
        dispatch_spec(spec, user=None, params={})

    message = str(info.value)
    assert "a mapping has no model and no primary key" in message
    assert "Return model instances or a QuerySet" in message


@pytest.mark.django_db
def test_mixed_instance_and_mapping_rows_are_each_answered() -> None:
    _world()
    first = Post.objects.order_by("id").first()
    rows = dispatch_spec(
        _listing(lambda: [first, {"id": 0}], affordances={"a": ONLY_CALLABLE}),
        user=None,
        params={},
    ).value
    assert rows[0] is first
    assert rows[0].affordance__a__books_closed is True
    assert rows[1] == {"id": 0, "affordance__a__books_closed": True}


# --- refusals ----------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "row", [SimpleNamespace(id=1), 7, None], ids=["plain-object", "int", "none"]
)
def test_a_row_that_is_neither_an_instance_nor_a_mapping_is_refused(row: Any) -> None:
    spec = _listing(lambda: [row], affordances={"a": ONLY_CALLABLE})

    with pytest.raises(ImproperlyConfigured, match="returned a row of type") as info:
        dispatch_spec(spec, user=None, params={})
    assert "mapping has no model" not in str(info.value)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "result", [{"count": 3}, 3, "rows", b"rows"], ids=["mapping", "int", "string", "bytes"]
)
def test_a_list_result_that_is_not_an_iterable_of_rows_is_refused(result: Any) -> None:
    spec = _listing(lambda: result, affordances={"a": ONLY_CALLABLE})

    with pytest.raises(ImproperlyConfigured, match="neither a QuerySet nor an iterable of rows"):
        dispatch_spec(spec, user=None, params={})


# --- async ----------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_async_a_list_of_instances_is_answered_off_the_loop_and_agrees() -> None:
    """The answer query runs inside the shaping hop; on the event loop it would raise
    ``SynchronousOnlyOperation`` instead of returning."""
    await sync_to_async(_world, thread_sensitive=True)()

    result = await adispatch_spec(_listing(lambda: list(_posts())), user=None, params={})
    queried = await sync_to_async(
        lambda: list(dispatch_spec(_listing(_posts), user=None, params={}).value),
        thread_sensitive=True,
    )()

    assert [_first_unmet(row, "publish") for row in result.value] == [
        _first_unmet(row, "publish") for row in queried
    ]
