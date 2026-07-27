# Declare specs once, project them to many transports

A spec is transport-neutral by design: the same `ServiceSpec` / `SelectorSpec`
drives an HTTP view, an MCP tool, a Pydantic-AI tool, a management command. But
each of those transports needs to be *told* which specs it exposes, so a project
serving two or three of them ends up writing the same list two or three times:

```python
# somewhere in the MCP wiring
mcp.register_selector_tool(name="list_orders", spec=orders_spec)
mcp.register_service_tool(name="refund_order", spec=refund_spec)

# and again in the agent wiring
toolset = SpecToolset(specs={"list_orders": orders_spec, "refund_order": refund_spec})
```

The lists drift — a new spec gets wired into one transport and forgotten in the
other — and the same operation can end up named differently in each. There is
also no single object to answer "what operations does this project expose?".

`SpecRegistry` is that object.

## One declaration site

```python
from rest_framework_services import SpecRegistry

registry = SpecRegistry()
registry.register("list_orders", orders_spec, tags=("read", "public"))
registry.register("get_order", order_detail_spec, tags=("read", "public"))
registry.register("refund_order", refund_spec, tags=("write", "admin"))
```

Each transport then reads that one source. Adoption needs no support from the
adapter: `specs()` returns the `dict[str, spec]` they already accept.

```python
toolset = SpecToolset(specs=registry.specs())
```

A registry holds only what is **invariant** across transports — which spec, its
canonical name, its tags. Everything transport-specific stays where it already
lives, at the binding that configures it: MCP annotations and output schemas, an
agent toolset's pagination or query-param registrations. That split is the point;
a registry is a *source* for a transport's own binding table, not a replacement
for one, and it introduces no layer between a caller and
[`dispatch_spec`](../reference/dispatch.md).

## Where the instance lives

There is no global registry, and none is coming. Your project owns the instance —
most simply as a module attribute populated from an `AppConfig.ready()`:

```python
# orders/registry.py
from rest_framework_services import SpecRegistry

registry = SpecRegistry()


# orders/apps.py
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "orders"

    def ready(self) -> None:
        from orders import specs
        from orders.registry import registry

        registry.register("list_orders", specs.orders_spec, tags=("read", "public"))
        registry.register("refund_order", specs.refund_spec, tags=("write", "admin"))
```

Registering a name twice raises rather than overwriting, so a copy-pasted
declaration fails at import time instead of silently shadowing an operation.

## Many registries, on purpose

A deployment that mounts more than one endpoint needs those endpoints to differ,
and to not share state. Both shapes of "more than one" work.

**Independent registries** — separate name namespaces, full isolation. Names are
unique *within* a registry, so two independent registries may reuse one:

```python
internal = SpecRegistry()
internal.register("purge_tenant", purge_spec)

public = SpecRegistry()
public.register("list_orders", orders_spec)

# /internal/mcp reads `internal`; /public/mcp reads `public`.
```

**One registry, many projections** — one declaration site, filtered views:

```python
registry = SpecRegistry()
registry.register("list_orders", orders_spec, tags=("read", "public"))
registry.register("refund_order", refund_spec, tags=("write", "admin"))

public_specs = registry.by_tag("public").specs()
admin_specs = registry.by_tag("admin").specs()
```

`by_tag` matches **any** of the tags given, so intersection composes from it
(`registry.by_tag("read").by_tag("public")`) while a union would not compose from
an intersection. `subset("a", "b")` selects by name instead, and raises on a name
that isn't registered — naming entries explicitly means a typo is a configuration
error, not a quietly smaller surface.

Every derivation returns a **new** registry holding a snapshot: the entries are
selected at call time and the spec objects are shared, not copied. So a view
never mutates its source, and registering on the source afterwards does not
appear in a view derived earlier.

```python
public = registry.by_tag("public")
registry.register("list_refunds", refunds_spec, tags=("public",))

assert "list_refunds" not in public   # `public` was a snapshot
```

`merge()` goes the other way, combining registries into a new one. It is the only
place cross-registry name uniqueness is enforced — reusing a name across
independent surfaces is legal until you actually put them together:

```python
combined = internal.merge(public)   # a shared name here raises
```

## Tags

Tags are free-form labels, and each transport maps them to its own vocabulary —
an MCP binding might turn `"destructive"` into a tool annotation, an agent
capability might use `"admin"` to decide which toolset an operation joins.

Keep them boolean-ish: `"read"`, `"write"`, `"admin"`, `"public"`,
`"destructive"`. A structured payload — a URI, a schema fragment, a config dict —
does not belong encoded in a tag string. It is almost always transport-specific,
which means it belongs at the binding, next to the other per-transport knobs.

## Reading a registry back

Beyond `specs()`, a registry introspects:

```python
len(registry)                 # how many operations
"refund_order" in registry    # is this name taken
registry.get("refund_order")  # the RegisteredSpec, or None
registry.all()                # every entry, in registration order
registry.mutations()          # the ServiceSpec entries
registry.queries()            # the SelectorSpec entries
```

Order is registration order everywhere, so a transport's tool listing is stable
between processes rather than reshuffling for clients that diff it.

`mutations()` and `queries()` discriminate by type rather than by a stored flag,
and together they cover every entry — which is why `register()` rejects anything
that is neither a `ServiceSpec` nor a `SelectorSpec`. A `PolymorphicServiceSpec`
is rejected too, with a pointer at the supported shape: register each of its
variants under its own name, since a transport that projects one operation per
name wants one operation per variant, not a union.

Full signatures in the [spec registry reference](../reference/registry.md).
