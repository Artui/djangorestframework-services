# Typing services and selectors

The framework wires a service or selector to a view by inspecting its
signature at request time and passing only the parameters it declares from
a known kwargs **pool**:

| Action                       | Pool keys                                                 |
| ---------------------------- | --------------------------------------------------------- |
| Create                       | `request`, `user`, `data`*, plus extras                   |
| Update / partial update      | `request`, `user`, `instance`, `data`*, plus extras       |
| Destroy                      | `request`, `user`, `instance`, `data`*, plus extras       |
| List selector                | `request`, `user`, URL kwargs, plus extras                |
| Retrieve selector            | `request`, `user`, URL kwargs, plus extras                |
| `output_selector`            | `request`, `user`, `result`, the action's pool, extras    |

`*` `data` is only present when `ServiceSpec.input_serializer` is set.

`view` is intentionally **not** in the pool. Services and selectors should
be plain business logic; pipe view state through a kwargs provider instead
(see below).

---

## What the framework guarantees statically

`ServiceSpec` and `SelectorSpec` are generic over the input / result type
and an optional `TypedDict` describing extra kwargs:

```python
class ServiceSpec(Generic[InputT, ResultT, ExtraT]): ...
class SelectorSpec(Generic[ResultT, ExtraT]): ...
```

Without parameterization, `ServiceSpec(service=fn)` keeps working — the
generic params default to `Any`, exactly as before.

Parameterizing them lets type checkers connect the input serializer, the
service signature, and the output:

```python
spec: ServiceSpec[AuthorIn, Author] = ServiceSpec(
    service=create_author,
    input_serializer=AuthorIn,
)
```

---

## Unified service / selector Protocols

Each Protocol is parameterised on input / instance / result types only.
`**extras` is typed `Any` so the framework's kwargs pool flows through —
services declare only the parameters they read:

```python
from rest_framework_services import CreateService

def create_author(
    *,
    data: AuthorIn,
    **extras,                     # request, user, tenant_id, ... — not enforced
) -> Author: ...

# Static check that the function matches the Protocol shape:
_check: CreateService[AuthorIn, Author] = create_author
```

For **strict-typed extras** — when you want the type checker to assist on
`extras["tenant_id"]` accesses — declare a `TypedDict` and unpack it into
*your function* via [PEP 692](https://peps.python.org/pep-0692/)
`Unpack[TypedDict]`. The typing lives on the function signature, not on
the Protocol; pair with the
[`@implements`](#attaching-the-protocol-to-the-function-implements)
decorator to attach the structural assertion at the def site:

```python
from typing_extensions import TypedDict, Unpack

from rest_framework_services import CreateService, implements

class CreateAuthorKwargs(TypedDict, total=False):
    tenant_id: int

@implements(CreateService[AuthorIn, Author])
def create_author(
    *,
    data: AuthorIn,
    **extras: Unpack[CreateAuthorKwargs],
) -> Author:
    tenant_id = extras.get("tenant_id")    # typed as int | None
    ...
```

`total=False` (or per-field `NotRequired`) is required so the function
stays Protocol-conformant — under PEP 692, any required key would make
the function reject callers that omit it, breaking the structural check
against `CreateService` / `ListSelector` / etc.

Drift between `create_author` and the parameterised Protocol produces a
`ty` / `pyright` error at the `@implements(...)` line. Drift inside the
function body (e.g. typo'd `extras["tenent_id"]`) is caught by the
TypedDict's normal key check.

The Protocols deliberately do **not** name `request` or `user` in their
fixed signature. The framework still puts both keys in the kwargs pool —
services that read them either pick them up off `**extras` directly, or
unpack a `HttpExtras[YourUser]` subclass (a `total=False` `TypedDict`)
for typed access:

```python
from rest_framework_services import CreateService, HttpExtras, implements
from typing_extensions import Unpack

class CreateAuthorKwargs(HttpExtras[MyUser], total=False):
    tenant_id: int

@implements(CreateService[AuthorIn, Author])
def create_author(
    *,
    data: AuthorIn,
    **extras: Unpack[CreateAuthorKwargs],
) -> Author:
    user = extras.get("user")           # typed as MyUser | None
    request = extras.get("request")     # typed as Request | None
    ...
```

Available Protocols:

- `CreateService[InputT, ResultT]`
- `UpdateService[InputT, InstanceT, ResultT]`
- `DeleteService[InputT, InstanceT, ResultT]` — bind `InputT` to your
  input dataclass for delete-with-payload, or to
  [`NoInput`](reference/types.md#noinput) when no body is read.
- `ListSelector[ResultT]`
- `RetrieveSelector[ResultT]`
- `OutputSelector[InT, OutT]`

!!! note "Migrating from `Strict*` / 3-arg parameterisation (0.6 – 0.10)"
    Releases 0.6 – 0.10 shipped separate `StrictCreateService` /
    `StrictListSelector` / … Protocols that carried an explicit
    `ExtraT` `TypedDict` as a type argument. **0.11 removes those
    classes** and folds them into the unified Protocols above, with
    the kwargs-shape moved off the Protocol and onto the function
    signature instead. The cross-Protocol enforcement of `ExtraT`
    only ever worked under one minor version of `ty` (0.0.32) — `mypy`
    and `pyright` always rejected the `Unpack[<TypeVar>]` pattern.
    Strict-typed extras now live on your function via
    `**extras: Unpack[YourKw]`, which type-checks consistently on
    every modern checker.

    Migration: rename `Strict*` to the unified name, drop the trailing
    `ExtraT` from every parameterisation site, and ensure your extras
    `TypedDict` declares its keys as `NotRequired` / `total=False`:

    ```python
    # Before (0.10)
    @implements(StrictCreateService[AuthorIn, MyKw, Author])
    def create_author(*, data: AuthorIn, **extras: Unpack[MyKw]) -> Author: ...

    # After (0.11)
    class MyKw(TypedDict, total=False):
        tenant_id: int

    @implements(CreateService[AuthorIn, Author])
    def create_author(*, data: AuthorIn, **extras: Unpack[MyKw]) -> Author: ...
    ```

    There is no deprecation bridge.

---

## Per-spec `kwargs` providers — drop the `if self.action == ...` chain

A viewset with several actions whose services take different extras has
historically had only one hook (`get_service_kwargs`), forcing branches like
`if self.action == "create": ...`. Move the contract onto the spec instead:

```python
from rest_framework_services import ServiceSpec, ServiceViewSet
from typing import TypedDict

class CreateAuthorKwargs(TypedDict):
    tenant_id: int

def _create_author_kwargs(view: ServiceView, request: Request) -> CreateAuthorKwargs:
    return {"tenant_id": request.tenant.id}

class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=AuthorIn,
            kwargs=_create_author_kwargs,    # per-spec, typed
        ),
        "publish": ServiceSpec(
            service=publish_author,
            kwargs=_publish_author_kwargs,   # different TypedDict
        ),
    }
```

The kwargs callable receives the `view` (typed as the narrow `ServiceView`
Protocol — exposes `request`, `kwargs`, `action`) and the current `request`,
and returns the `TypedDict` it declares. Services and selectors stay
business-logic-pure; `view` is only available where it makes sense — at
the framework-glue boundary.

### Resolution order

When the framework needs to assemble extras for a call, it merges three
layers (later overrides earlier — most specific wins):

1. **Catch-all hook** — `view.get_service_kwargs(self)` /
   `view.get_selector_kwargs(self)`. The existing global fallback.
2. **Per-action hook** — `view.get_<action>_service_kwargs(self)` /
   `view.get_<action>_selector_kwargs(self)`. Discoverable in IDEs;
   no `if self.action == ...` needed.
3. **Per-spec callable** — `ServiceSpec.kwargs` / `SelectorSpec.kwargs`.
   The most specific level; co-located with the service it feeds.

Use whichever level matches the granularity of your contract.

---

### Attaching the Protocol to the function: `@implements`

The recommended way to assert that a callable matches a strict Protocol is
the [`implements`](reference/services.md#rest_framework_services.implements.implements)
decorator — it returns the function unchanged at runtime and triggers the
structural-subtyping check at the decorator line:

```python
@implements(ListSelector[Author])
def list_authors(
    **extras: Unpack[ListAuthorsKwargs],
) -> Iterable[Author]: ...
```

The legacy throwaway-variable form still works and is sometimes useful for
ad-hoc one-off checks:

```python
def list_authors(...) -> Iterable[Author]: ...

_check: ListSelector[Author] = list_authors
```

A few notes on type-checker support:

- **`ty`** validates `@implements(...)` against the parameterized Protocols;
  the decorator is the form CI exercises in this repo.
- **`mypy`** rejects `type[Protocol]` arguments under its `type-abstract`
  rule, so `@implements(...)` triggers a `[type-abstract]` error in mypy.
  Either add `# type: ignore[type-abstract]` next to the decorator or stick
  with the `_check: ...` shim form when mypy is your primary checker.
- **PEP 692 support across checkers is uneven** — drift detection on
  `**extras: Unpack[TypedDict]` works best when the function uses the
  `Unpack[...]` form (matching the Protocol). Strict parameterization still
  catches drift on the fixed pool keys (`data`, `instance`, return type) in
  every supported checker.

---

## Fail-fast validation at `as_view()`

`as_view()` walks every spec and validates that the callable's signature
can be satisfied. Misconfigured specs raise
[`ImproperlyConfigured`](https://docs.djangoproject.com/en/stable/ref/exceptions/#django.core.exceptions.ImproperlyConfigured)
at URL-wiring time instead of with a generic `TypeError` at the first
request:

- Service requires `data` but the spec has no `input_serializer`.
- Service requires `instance` but the action is create / list (no instance).
- Output selector requires a key that's only present in service-call context.
- A required parameter is not framework-provided and the view has no
  `kwargs=` provider, no `get_<action>_*_kwargs`, and no `get_*_kwargs` —
  the parameter would be silently dropped at request time.

The validator is permissive when the user has plugged in an extras source
(the framework can't statically introspect what those provide). Strict
parameterization (above) covers that gap on the static side.
