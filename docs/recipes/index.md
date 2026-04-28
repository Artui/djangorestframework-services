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
- **[Scaffold a service app](scaffold-app.md)** — `startserviceapp` and
  the convention behind `services/`, `selectors/`, `validators/`.
