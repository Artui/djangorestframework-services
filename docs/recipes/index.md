# Recipes

Task-shaped how-tos. Each one is self-contained and assumes you've read
the [Quickstart](../quickstart.md) and [Concepts](../concepts.md).

- **[Custom action with `@service_action`](service-action.md)** — add a
  non-CRUD action to a viewset and route it through the same
  validate-dispatch-render flow as the standard mutations.
- **[Compose your own viewset](compose-viewset.md)** — pick the per-
  action mixins you actually need instead of starting from
  `ServiceViewSet`.
- **[Pass extra kwargs to services](extra-kwargs.md)** — surface a
  tenant, a feature flag, or a test clock to your services without
  threading it through `request`.
- **[Per-action permissions on the spec](permissions.md)** — set
  `permission_classes` per action without `if self.action == ...`
  branching in `get_permissions()`.
- **[Per-spec queryset shaping](queryset-shaping.md)** — add
  `select_related` / `prefetch_related` / `annotations` to a selector
  spec, plus an `extend_queryset` callable for request-dependent
  shaping.
- **[Filter a selector with `filter_set`](selector-filtering.md)** — point
  a `django-filter` FilterSet at a list or retrieve selector's queryset,
  the "replaces `DjangoFilterBackend`" rule, and the boundary with
  `kwargs` for computed (non-queryset) results.
- **[Customise serializer context](serializer-context.md)** — direction-
  and action-specific hooks for `get_serializer_context()`, so the input
  and output serializers can see different keys.
- **[Scaffold a service app](scaffold-app.md)** — `startserviceapp` and
  the convention behind `services/`, `selectors/`, `validators/`.
