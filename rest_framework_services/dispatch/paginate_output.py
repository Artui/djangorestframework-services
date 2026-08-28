"""``paginate_output`` — slice a list selector's rows into one agent-facing page."""

from __future__ import annotations

from typing import Any

from rest_framework_services.selectors.utils import is_queryset
from rest_framework_services.types.output_page import OutputPage

#: Rows per page when the caller asks for a page and names no size.
DEFAULT_PAGE_SIZE = 100


def paginate_output(
    rows: Any,
    *,
    page: int | None = None,
    limit: int | None = None,
    max_page_size: int | None = None,
) -> OutputPage:
    """Slice ``rows`` into the page an agent transport serves.

    ``page`` / ``limit`` default to 1 and
    [`DEFAULT_PAGE_SIZE`][rest_framework_services.dispatch.paginate_output.DEFAULT_PAGE_SIZE].
    Out-of-range values clamp at *both* ends — ``limit`` down to
    ``max_page_size`` and up to 1, ``page`` up to 1 and down to the last page
    that exists — and the clamps are not silent the way truncating an
    unpaginated result would be: ``totalPages`` / ``hasNext`` are computed from
    the clamped ``limit``, and the returned ``page`` is the one actually served.
    A caller that asked for 500 rows and got 100, or for page 10 of 3, is told
    what it received.

    Both values are taken already parsed. Turning an untyped argument into an
    integer is where the transports legitimately differ — a public endpoint
    clamps a malformed value and answers, an in-process toolset can hand the
    model its mistake back and ask again — and that is a policy about bad input,
    not about what a page is.

    The upper clamp on ``page`` is why ``total`` is counted *before* the slice:
    ``(page - 1) * limit`` on an unclamped page is an arbitrarily large SQL
    ``OFFSET``, which a backend either scans towards or rejects outright with a
    ``DatabaseError`` this does not catch.

    Raises:
        TypeError: If ``rows`` is neither a queryset nor a sized, sliceable
            sequence — there is nothing to count and nothing to slice.
    """
    served_limit: int = max(1, DEFAULT_PAGE_SIZE if limit is None else limit)
    if max_page_size is not None:
        served_limit = min(served_limit, max_page_size)
    total: int = _count(rows)
    # Clamped against the ``totalPages`` the envelope will report, so the page
    # served is never one the same payload then says does not exist.
    served_page: int = min(max(1, 1 if page is None else page), max(1, -(-total // served_limit)))
    start: int = (served_page - 1) * served_limit
    return OutputPage(
        items=rows[start : start + served_limit],
        page=served_page,
        limit=served_limit,
        total=total,
    )


def _count(rows: Any) -> int:
    """How many rows there are, without materializing a queryset.

    Discriminated with ``is_queryset``, **not** ``hasattr(rows, "count")``:
    ``list`` and ``tuple`` expose ``.count`` too, but it is ``.count(value)``
    and needs an argument, which turns a list-returning paginated selector into
    an opaque ``count() takes exactly one argument``.
    """
    if is_queryset(rows):
        return int(rows.count())
    if hasattr(rows, "__len__") and hasattr(rows, "__getitem__"):
        return len(rows)  # a plain sequence, paginated in memory
    raise TypeError(
        "A paginated selector must return a QuerySet or a sized, sliceable sequence "
        f"(list / tuple); got {type(rows).__name__}. Return a sliceable collection, "
        "or serve this selector unpaginated."
    )


__all__ = ["DEFAULT_PAGE_SIZE", "paginate_output"]
