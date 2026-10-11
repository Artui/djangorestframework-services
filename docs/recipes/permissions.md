# Per-action permissions on the spec

DRF's `permission_classes` on the view applies to every action a viewset
serves. When the permissions actually vary per action — `list` is public,
`create` requires authentication, an `approve` custom action is admin-only —
the usual workaround is an `if self.action == ...` branch in
`get_permissions()`. The same dispersion `action_specs` was designed to
remove.

Set `permission_classes` on the spec instead. It overrides the view's
class-level `permission_classes` for that one action; everything else
falls through to the view default.

> The value is a sequence of `BasePermission` **subclasses** (not
> instances), or of DRF's composed operands built from them — the same
> entries `APIView.permission_classes` takes. Misconfigurations fail fast at
> `as_view()` time with `ImproperlyConfigured`.

## Composing permissions

DRF's `|`, `&` and `~` work on a spec exactly as on a view, so "staff, or a
caller holding the service key" needs no hand-written `IsStaffOrHasKey` class:

```python
SelectorSpec(
    kind=SelectorKind.LIST,
    selector=list_statistics,
    output_serializer=StatisticSerializer,
    permission_classes=[IsStaff | HasServiceKey],
)
```

An instance anywhere inside the expression (`IsStaff | HasServiceKey()`) is
refused at `as_view()` like a bare one. The capability manifest lists a
composed entry as one guard whose `source` is the expression,
`"(app.permissions.IsStaff | app.permissions.HasServiceKey)"`.

Two DRF behaviours carry over unchanged. `~` inverts `has_object_permission`
as well as `has_permission`, and a class that does not override the former
answers `True` there — so `~IsBlocked` refuses every object-level check unless
`IsBlocked` defines it. And before DRF 3.15 two operands are equal only if they
are the same object, so `permission_strategy="require_identical"` on a
`PolymorphicServiceSpec` needs the variants to share one composed operand (a
module constant) rather than each spelling `A | B`.

## Per-action permissions on a viewset

```python
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    service_action,
)


class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    permission_classes = [IsAuthenticated]  # view-level default

    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorSerializer,
            permission_classes=[AllowAny],  # public listing
        ),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=AuthorSerializer,
            ),
            # falls through to view-level IsAuthenticated
        ),
        "destroy": ServiceSpec(
            service=delete_author,
            permission_classes=[IsAdminUser],  # admin only
        ),
    }

    @service_action(
        ServiceSpec(
            service=approve_author,
            input_serializer=ApproveInput,
            permission_classes=[IsAdminUser],  # custom action, admin only
        ),
        detail=True,
        methods=["post"],
    )
    def approve(self, request, pk=None):
        """Stubbed — handler supplied by @service_action."""
```

## Standalone views

The same field works on the standalone `Service*View` and `Selector*View`
classes. The spec wins over the view's class-level `permission_classes`;
`None` (the default) inherits.

```python
class CreateAuthorView(ServiceCreateView):
    spec = ServiceSpec(
        service=create_author,
        input_serializer=CreateAuthorInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=AuthorSerializer,
        ),
        permission_classes=[IsAuthenticated],
    )
```

## Empty sequence vs. `None`

- `permission_classes=None` (the default) — **inherit** the view's
  class-level permissions.
- `permission_classes=[]` — **no permissions** for this action explicitly.
  Useful to open a single endpoint on an otherwise-locked-down viewset
  without touching the global `DEFAULT_PERMISSION_CLASSES`.

## How the resolution works

For viewsets that compose `_ActionSpecsMixin` (every mixin in this package,
plus `ServiceViewSet` / `SelectorViewSet`), `get_permissions()`:

1. Looks up `action_specs[self.action]`. If the entry has
   `permission_classes`, those win.
2. Otherwise looks at the bound action handler. Decorator-attached specs
   (`@service_action(spec)`, `@selector_action(spec)`) live there, so their
   `permission_classes` are honored even without the DRF router.
3. Otherwise falls back to `super().get_permissions()` — the standard DRF
   lookup of the view's class-level `permission_classes`.

Standalone views (`ServiceCreateView`, `SelectorListView`, etc.) follow the
same precedence: spec wins, then fall through to the view.

When you wire the viewset through DRF's router, `@service_action` /
`@selector_action` also carry their `permission_classes` via DRF's standard
`@action(permission_classes=...)` integration — so the same configuration
works in both router-driven and direct-`as_view` setups.

**A `PolymorphicServiceSpec` is the exception.** `@action` takes a single
list, and which list applies to a polymorphic action depends on its
`permission_strategy` — under `"discriminate"`, on the request body. There is
nothing to forward, so the variants' `permission_classes` are enforced only by
`_ActionSpecsMixin.get_permissions`. That makes the mixin a requirement for
that combination rather than a convenience: compose `ServiceViewSet` (or any
mixin from this package) and it is already there. On a viewset without it,
`@service_action` refuses the request instead of serving it under the view's
default permissions — the decorated class is unknown at decoration time, so
the check cannot happen any earlier.
