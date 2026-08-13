"""``SelectorKind`` — discriminates list-shaped vs retrieve-shaped selectors."""

from __future__ import annotations

from enum import Enum


class SelectorKind(str, Enum):
    """Whether a [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] returns many objects or a single one.

    The kind is what tells the framework — and any future caller that
    reuses a spec outside an HTTP request — whether to materialize the
    selector's return as a collection (``LIST``) or as a single instance
    with retrieve-flavoured 404 semantics (``RETRIEVE``). Mounting a spec
    on a mismatched view (e.g. a ``LIST`` spec on a
    [`SelectorRetrieveView`][rest_framework_services.views.query.selector_retrieve_view.SelectorRetrieveView]) raises
    ``ImproperlyConfigured`` at ``as_view()``
    time.

    Inheriting from ``str`` keeps the value JSON-serializable and
    print-friendly while still behaving as a proper enum for ``is`` /
    ``==`` checks.
    """

    LIST = "list"
    RETRIEVE = "retrieve"
