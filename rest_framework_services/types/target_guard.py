"""``TargetGuard`` — the object-permission hook ``dispatch_spec`` calls per target."""

from __future__ import annotations

from typing import Any, Protocol

from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class TargetGuard(Protocol):
    """Invoked with the resolved target before a mutation runs; raise to abort.

    ``dispatch_spec`` deliberately does **not** consult ``permission_classes``
    (authorization is the caller's job — which is why
    :func:`~rest_framework_services.enforce_permissions` ships separately). But
    object-level checks need the resolved instance, which only ``dispatch_spec``
    sees, *before* the service runs. So ``dispatch_spec`` exposes this hook
    rather than folding authz in: it stays authz-agnostic, only invoking a
    caller-supplied guard.

    The signature is deliberately **identical to**
    :func:`~rest_framework_services.enforce_permissions`, so the canonical wiring
    is passing that function directly, by name — not wrapping it in a ``lambda``::

        dispatch_spec(spec, ..., on_target_resolved=enforce_permissions)

    A consumer needing custom logic writes a module-level function with the same
    shape and passes it the same way; ``ty`` enforces conformance.

    ``dispatch_spec`` builds the :class:`OfflineContext` itself (it already holds
    ``user`` / ``request`` / ``view``) and calls ``guard(spec, context,
    instance=target)``. ``instance`` is the resolved row for an update, the
    resolved set for a collection (bulk) mutation, and ``None`` for a create — so
    the guard fires uniformly on every resolved target, running the class-level
    check when there is no object.
    """

    def __call__(
        self,
        spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
        context: OfflineContext,
        *,
        instance: Any = None,
    ) -> None: ...
