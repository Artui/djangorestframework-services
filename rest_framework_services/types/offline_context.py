"""``OfflineContext`` — the synthesized request / view / user for off-HTTP dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.request import Request

from rest_framework_services.types.offline_service_view import OfflineServiceView


@dataclass(frozen=True)
class OfflineContext:
    """The HTTP-surrogate a spec needs when dispatched off the HTTP path.

    Produced by :func:`~rest_framework_services.dispatch.build_offline_context`.
    Its ``request`` / ``view`` feed the optional ``request=`` / ``view=``
    arguments of :func:`~rest_framework_services.dispatch_spec`, and the whole
    value is consumed by :func:`~rest_framework_services.enforce_permissions`.

    - ``user`` — the acting principal (``request.user`` is set to the same
      value); ``Any`` because the user model is project-defined.
    - ``request`` — the synthetic DRF Request (``.user`` set, ``.data`` carrying
      the params), forwarded to spec callables that declare ``request``.
    - ``view`` — the :class:`OfflineServiceView` forwarded to callables that
      declare ``view`` and used as the ``view`` argument of permission checks.
    """

    user: Any
    request: Request
    view: OfflineServiceView
