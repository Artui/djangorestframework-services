"""``unguarded_specs`` — which specs have no authorization of their own."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

Spec = ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]


def unguarded_specs(specs: Mapping[str, Spec]) -> list[str]:
    """Return the names of every spec with **nothing to inherit off HTTP**.

    A spec whose ``permission_classes`` is ``None`` is not misconfigured — on
    HTTP it means *inherit*, and the view's own ``permission_classes`` and
    ``DEFAULT_PERMISSION_CLASSES`` supply the answer. **Off HTTP neither
    exists.** There is no view to inherit from and no framework default in the
    path, so the same spec that is correctly guarded behind a DRF view becomes
    an unguarded operation the moment a toolset or an MCP server exposes it.
    :func:`~rest_framework_services.enforce_permissions` returns early on
    ``None`` for exactly that reason — it has nothing to enforce.

    ⭐ **Why this lives here and not in each transport.** The ambiguity is a
    *drf-services* fact: this package owns the off-HTTP dispatch semantics that
    create it, so the knowledge of what "unguarded" means belongs with them
    rather than being re-derived per transport. ⇒ *a check belongs wherever the
    fact it checks is defined, not wherever the failure is felt.*

    ⚠ **This raises nothing and defaults nothing, deliberately.** It answers a
    question; it does not set policy. Whether an unguarded spec is a hard error,
    a warning, or acceptable — and what the message says — is the transport's
    call, exactly as ``dispatch_spec`` stays authorization-agnostic while
    ``enforce_permissions`` is available for a caller that wants it. A transport
    fails its own way::

        missing = unguarded_specs(registry.specs)
        if missing and self.require_permissions:
            raise ImproperlyConfigured(
                f"These specs expose no permission_classes: {', '.join(missing)}."
            )

    Called at registration or construction rather than per request: an unguarded
    tool is a deployment defect, and failing the deploy beats widening every
    request that follows.

    Takes the mapping a registry already holds. A caller with ``(name, spec)``
    pairs passes ``dict(pairs)`` — insertion order survives, so either way the
    message a transport builds lists specs in the order they were declared,
    which is easier to act on than alphabetical.

    ⚠ Deliberately **not** a ``Mapping | Iterable[tuple[...]]`` union: a mapping
    *is* an iterable, so the two arms overlap and a mapping keyed by tuples
    satisfies both. Narrowing that union is guesswork, and the one shape every
    caller already has is the mapping.
    """
    return [name for name, spec in specs.items() if spec.permission_classes is None]


__all__ = ["unguarded_specs"]
