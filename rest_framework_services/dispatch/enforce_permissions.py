"""``enforce_permissions`` — run a spec's ``permission_classes`` off the HTTP path."""

from __future__ import annotations

from typing import Any

from django.db.models import Model
from rest_framework.exceptions import PermissionDenied

from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def enforce_permissions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    context: OfflineContext,
    *,
    instance: Any = None,
) -> None:
    """Enforce ``spec.permission_classes`` against an off-HTTP context.

    Mirrors what a DRF view does on the HTTP path —
    ``[perm() for perm in spec.permission_classes]`` then
    ``perm.has_permission(request, view)`` — but against the synthetic request
    and view from :func:`build_offline_context`. ``dispatch_spec`` deliberately
    does **not** consult ``permission_classes`` (authorization is the view's job
    on HTTP), so an off-HTTP transport must call this itself before dispatching,
    or it would skip authorization entirely.

    When ``instance`` is a Django ``Model``, object-level permissions
    (``has_object_permission``) are also checked — matching the HTTP path's
    ``check_object_permissions`` against a resolved instance. (drf-mcp's adapter
    omits this; off-HTTP parity restores it.) A non-``Model`` ``instance`` — most
    importantly the collection queryset a bulk / LIST dispatch resolves — runs
    only the class-level check, never ``has_object_permission``: object
    permissions are a per-row concept (the BULK decision authorizes per-set, not
    per-row), and ``has_object_permission(request, view, <QuerySet>)`` would
    ``AttributeError`` or silently mis-authorize. This makes
    ``on_target_resolved=enforce_permissions`` the safe canonical guard for
    *every* dispatch mode.

    ``spec.permission_classes is None`` (the default — "inherit the view's
    class-level permissions" on HTTP) is a **no-op** off-HTTP: there is no view
    class to inherit from, so the transport owns any default policy. An empty
    sequence is likewise a no-op ("no permissions").

    Raises :exc:`~rest_framework.exceptions.PermissionDenied` (403) on the first
    failing permission, carrying that permission's ``message`` / ``code`` when it
    declares them — the same surface a DRF view produces.

    Permission classes that read DRF ``APIView`` attributes beyond ``request`` /
    ``action`` / ``kwargs`` (e.g. ``DjangoModelPermissions``, which inspects
    ``view.queryset``) are not supported off-HTTP.
    """
    if spec.permission_classes is None:
        return
    request = context.request
    # DRF permissions take ``has_permission(request, view)`` and type ``view`` as
    # ``APIView``; ``OfflineServiceView`` is the structural stand-in. ``Any`` at
    # the boundary keeps the call site clean without per-call ignores.
    view: Any = context.view
    for permission_class in spec.permission_classes:
        permission = permission_class()
        if not permission.has_permission(request, view):
            _deny(permission)
        if isinstance(instance, Model) and not permission.has_object_permission(
            request, view, instance
        ):
            _deny(permission)


def _deny(permission: Any) -> None:
    raise PermissionDenied(
        detail=getattr(permission, "message", None),
        code=getattr(permission, "code", None),
    )
