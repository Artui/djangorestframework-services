"""Tests for ``paginate_for_agent`` and the ``AgentPage`` it returns.

The behaviour here is ported, not invented: this shaper lived in
``rest_framework_mcp``'s selector dispatch, beside a schema that has always been
published from *this* package. Each case below pins something that
implementation had learned -- the clamps at both ends, the count before the
slice, and the ``.count`` that is not a count.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.dispatch.paginate_for_agent import (
    DEFAULT_AGENT_PAGE_SIZE,
    paginate_for_agent,
)
from tests.testapp.models import Author

ROWS = list(range(250))


def test_defaults_to_the_first_page_at_the_default_size() -> None:
    page = paginate_for_agent(ROWS)

    assert (page.page, page.limit, page.total) == (1, DEFAULT_AGENT_PAGE_SIZE, 250)
    assert page.items == ROWS[:100]


def test_a_limit_over_the_ceiling_is_clamped_down() -> None:
    page = paginate_for_agent(ROWS, limit=500, max_page_size=100)

    assert page.limit == 100
    assert len(page.items) == 100


def test_a_limit_under_the_ceiling_is_untouched() -> None:
    assert paginate_for_agent(ROWS, limit=25, max_page_size=100).limit == 25


def test_no_ceiling_leaves_a_large_limit_alone() -> None:
    assert paginate_for_agent(ROWS, limit=500).limit == 500


@pytest.mark.parametrize("limit", [0, -5])
def test_a_limit_below_one_is_clamped_up(limit: int) -> None:
    """Not rejected: a page of zero rows is a page nobody can page through."""
    assert paginate_for_agent(ROWS, limit=limit).limit == 1


@pytest.mark.parametrize("page", [0, -3])
def test_a_page_below_one_is_clamped_up(page: int) -> None:
    assert paginate_for_agent(ROWS, page=page, limit=10).page == 1


def test_a_page_past_the_end_clamps_to_the_last_one_that_exists() -> None:
    page = paginate_for_agent(ROWS, page=99, limit=100)

    assert (page.page, page.total_pages, page.has_next) == (3, 3, False)
    assert page.items == ROWS[200:]


def test_a_page_inside_the_range_is_untouched() -> None:
    page = paginate_for_agent(ROWS, page=2, limit=100)

    assert (page.page, page.has_next) == (2, True)
    assert page.items == ROWS[100:200]


def test_an_empty_result_is_one_empty_page() -> None:
    """Not zero pages: the page served *is* 1, so saying it does not exist
    contradicts the payload it arrives with."""
    page = paginate_for_agent([], page=7, limit=10)

    assert (page.page, page.total, page.total_pages, page.has_next) == (1, 0, 1, False)
    assert list(page.items) == []


def test_a_partial_last_page_is_still_the_last_page() -> None:
    page = paginate_for_agent(ROWS, page=3, limit=100)

    assert (page.total_pages, page.has_next, len(page.items)) == (3, False, 50)


def test_the_envelope_is_the_shape_the_schema_publishes() -> None:
    page = paginate_for_agent(ROWS, page=2, limit=100)

    assert page.envelope(["rendered"]) == {
        "items": ["rendered"],
        "page": 2,
        "totalPages": 3,
        "hasNext": True,
    }


def test_the_envelope_carries_rendered_rows_not_the_slice() -> None:
    """The projection lands on the rows; the envelope's own keys belong to no
    serializer, so the caller renders and hands the result back."""
    page = paginate_for_agent(ROWS, limit=2)

    assert page.envelope([{"id": 1}])["items"] == [{"id": 1}]


@pytest.mark.django_db
def test_a_queryset_is_counted_not_materialized() -> None:
    Author.objects.bulk_create([Author(name=f"a{index}") for index in range(5)])

    page = paginate_for_agent(Author.objects.order_by("id"), page=2, limit=2)

    assert (page.total, page.page, page.total_pages, page.has_next) == (5, 2, 3, True)
    assert [author.name for author in page.items] == ["a2", "a3"]


def test_a_tuple_is_paginated_in_memory() -> None:
    page = paginate_for_agent(tuple(ROWS), page=2, limit=100)

    assert page.total == 250
    assert page.items == tuple(ROWS[100:200])


def test_a_list_is_not_mistaken_for_a_queryset_by_its_count_attribute() -> None:
    """``list.count`` exists and takes an argument. Discriminating on
    ``hasattr(rows, "count")`` turned a list-returning paginated selector into an
    opaque ``count() takes exactly one argument``."""
    assert hasattr(ROWS, "count")

    assert paginate_for_agent(ROWS, limit=10).total == 250


def test_something_neither_counted_nor_sliced_says_so() -> None:
    def _generator() -> Any:
        yield 1

    with pytest.raises(TypeError, match="QuerySet or a sized, sliceable sequence"):
        paginate_for_agent(_generator())
