"""``OutputPage`` — one page of a list selector's rows, and how to describe it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutputPage:
    """One page of rows, plus what a caller needs to know to ask for the next.

    The envelope this describes is **already** the one
    [`output_to_json_schema`][rest_framework_services.jsonschema.output_to_json_schema.output_to_json_schema]
    publishes for ``kind=LIST, paginate=True``. That schema was here before
    anything in this package produced the payload it describes: the shaper lived
    in a transport, so one agent transport wrapped its pages and the other
    returned a bare list, against one schema that claimed the envelope for both.
    Two implementations of one mechanism drift; one of them was missing
    entirely.

    ``items`` is the slice itself, unrendered — rendering needs a view, a
    request and a spec, all of which belong to the caller. Hand the rendered
    result back to [`envelope`][rest_framework_services.types.output_page.OutputPage.envelope]
    to get the wire shape.
    """

    #: The rows on this page, as sliced — a queryset slice or a sequence slice.
    items: Any
    #: The page actually served, which is not always the page asked for.
    page: int
    #: The page size actually applied, after any ceiling.
    limit: int
    #: How many rows there are in total, counted before the slice.
    total: int

    @property
    def total_pages(self) -> int:
        """How many pages exist at this ``limit``. At least one, even for no rows.

        An empty result is one empty page rather than zero pages: ``page`` is
        1-based and the page served for an empty result is 1, so reporting 0
        would describe a page the caller was just handed as not existing.
        """
        return max(1, -(-self.total // self.limit))

    @property
    def has_next(self) -> bool:
        """Whether asking for ``page + 1`` would return anything."""
        return self.page < self.total_pages

    def envelope(self, rendered: Any) -> dict[str, Any]:
        """Wrap already-rendered rows in the published pagination envelope.

        ``rendered`` rather than ``items`` because the projection lands on the
        rows and never on the envelope: ``page`` / ``totalPages`` / ``hasNext``
        are this shape's own keys and belong to no serializer, so a projection
        walking them would look for markings that cannot exist.
        """
        return {
            "items": rendered,
            "page": self.page,
            "totalPages": self.total_pages,
            "hasNext": self.has_next,
        }


__all__ = ["OutputPage"]
