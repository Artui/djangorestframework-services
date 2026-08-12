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
    exists**, so the same spec becomes an unguarded operation the moment a
    toolset or an MCP server exposes it, and
    :func:`~rest_framework_services.enforce_permissions` returns early with
    nothing to enforce.

    This **raises nothing and defaults nothing, deliberately**: whether an
    unguarded spec is a hard error, a warning, or acceptable is the transport's
    call, exactly as ``dispatch_spec`` stays authorization-agnostic. A transport
    fails its own way::

        missing = unguarded_specs(registry.specs)
        if missing and self.require_permissions:
            raise ImproperlyConfigured(
                f"These specs expose no permission_classes: {', '.join(missing)}."
            )

    Call it at registration or construction rather than per request: an
    unguarded tool is a deployment defect, and failing the deploy beats
    widening every request that follows.

    Args:
        specs: The name → spec mapping a registry already holds. A caller with
            ``(name, spec)`` pairs passes ``dict(pairs)``.

    Returns:
        The unguarded names, in the mapping's insertion order — so a message
        built from them lists specs as they were declared.
    """
    return [name for name, spec in specs.items() if spec.permission_classes is None]


__all__ = ["unguarded_specs"]
