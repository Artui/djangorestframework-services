"""Synchronous selector protocol.

A selector is any callable used by the query views to fetch data. The
library deliberately does not enforce a signature — views resolve which
arguments to pass by inspecting the selector's signature, drawing from a
known pool (``filters``, ``request``, ``user``, ``view``, plus extras).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Selector(Protocol):
    """Any callable returning a queryset, list, or single object.

    Used as a structural type for documentation purposes; the views accept
    any plain callable, so satisfying this protocol is optional.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
