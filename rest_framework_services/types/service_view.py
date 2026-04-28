"""``ServiceView`` Protocol — the narrow surface a kwargs provider sees."""

from __future__ import annotations

from typing import Any, Protocol

from rest_framework.request import Request


class ServiceView(Protocol):
    """Minimal structural shape of a view as exposed to a kwargs provider.

    Per-spec kwargs providers (``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs``)
    receive the calling view typed as :class:`ServiceView`. The Protocol pins
    only the attributes a provider can rely on across both the standalone
    ``Service*View`` classes and the viewset mixins:

    - ``request`` — the current DRF :class:`~rest_framework.request.Request`.
    - ``kwargs`` — the URL kwargs dict resolved by the URLconf
      (e.g. ``{"pk": 7}``). Empty dict on routes without captured groups.
    - ``action`` — the viewset action name (``"create"``, ``"list"``, etc.)
      on viewsets; ``None`` on standalone single-purpose views.

    Keep the surface narrow on purpose: providers should translate view state
    into typed kwargs for the service, not reach for view internals.
    """

    request: Request
    kwargs: dict[str, Any]
    action: str | None
