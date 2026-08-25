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
- **[Nested writes for every relation kind](nested-writes.md)** — write a
  parent plus everything hanging off it from one request with `relations=`:
  forward FK / one-to-one, reverse collections, reverse one-to-one,
  many-to-many and generic relations, why key matching needs a `scope=`,
  why a nested create may not carry a primary key, and how to migrate off a
  writable-nested serializer.
- **[Bulk & collection mutations](bulk-mutations.md)** — `many=True` list
  in/out and a `collection_selector_spec` target for instance-less bulk
  delete / update over a filtered set.
- **[Customise serializer context](serializer-context.md)** — direction-
  and action-specific hooks for `get_serializer_context()`, so the input
  and output serializers can see different keys.
- **[Declare specs once for many transports](spec-registry.md)** — hold your
  spec set in a `SpecRegistry` so MCP, an agent toolset, and HTTP read one
  source; tags, filtered views, and several registries per project.
- **[Scaffold a service app](scaffold-app.md)** — `startserviceapp` and
  the convention behind `services/`, `selectors/`, `validators/`.
- **[Mark fields for an agent audience](agent-audience.md)** — keep one
  serializer for your API and your agent tools, and declare per field which
  are content, which are opaque handles, and which are plumbing a model
  should never read out.
