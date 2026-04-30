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

## Lenient Protocols (the default)

Annotate a service against the matching Protocol to get IDE / `ty` /
`mypy` help on the *known* pool keys:

```python
from rest_framework_services import CreateService

def create_author(
    *,
    data: AuthorIn,
    request: HttpRequest,
    user: UserT,           # AbstractBaseUser | AnonymousUser | None
    **extras: object,      # tenant_id, etc. — not enforced
) -> Author: ...

# Static check that the function matches the Protocol shape:
_check: CreateService[AuthorIn, Author] = create_author
```

`**kwargs: Any` on the Protocol is the escape hatch: services can declare
only the parameters they actually need and the framework passes nothing
else. Available lenient Protocols:

- `CreateService[InputT, ResultT]`
- `UpdateService[InputT, InstanceT, ResultT]`
- `DeleteService[InstanceT, ResultT]`
- `ListSelector[ResultT]`
- `RetrieveSelector[ResultT]`
- `OutputSelector[InT, OutT]`

---

## Per-spec `kwargs` providers — drop the `if self.action == ...` chain

A viewset with several actions whose services take different extras has
historically had only one hook (`get_service_kwargs`), forcing branches like
`if self.action == "create": ...`. Move the contract onto the spec instead:

```python
from rest_framework_services import ServiceSpec, ServiceViewSet
from rest_framework_services.types.service_spec import ServiceSpec  # generic
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

## Strict Protocols — fail on signature drift

Lenient Protocols accept `**kwargs: Any`, which is convenient but lets the
service signature drift from the actual contract. When you want the
opposite — `ty` / `mypy` should fail on any drift — parameterize against
the `Strict*` variants. They use [PEP 692](https://peps.python.org/pep-0692/)
`Unpack[TypedDict]` to pin extras exactly. Pair them with the
[`@implements`](#attaching-the-protocol-to-the-function-implements) decorator
to attach the assertion to the function definition itself:

```python
from typing import TypedDict
from typing_extensions import Unpack

from rest_framework_services import StrictCreateService, implements

class CreateAuthorKwargs(TypedDict):
    tenant_id: int

@implements(StrictCreateService[AuthorIn, CreateAuthorKwargs, Author])
def create_author(
    *,
    data: AuthorIn,
    request: HttpRequest,
    user: UserT,
    **extras: Unpack[CreateAuthorKwargs],   # exact extras contract
) -> Author: ...
```

Drift between `create_author` and the parameterized Protocol now produces a
`ty` error at the `@implements(...)` line.

Available strict Protocols:

- `StrictCreateService[InputT, ExtraT, ResultT]`
- `StrictUpdateService[InputT, InstanceT, ExtraT, ResultT]`
- `StrictDeleteService[InstanceT, ExtraT, ResultT]`
- `StrictListSelector[ExtraT, ResultT]`
- `StrictRetrieveSelector[ExtraT, ResultT]`
- `StrictOutputSelector[InT, ExtraT, OutT]`

`ExtraT` always sits immediately before the result type so the parameter
list reads "input, extras, result" — mirroring the call shape.

The strict and lenient variants are interchangeable at the
`ServiceSpec.service` field — pick the level of enforcement per service.

### Attaching the Protocol to the function: `@implements`

The recommended way to assert that a callable matches a strict Protocol is
the [`implements`](reference/services.md#rest_framework_services.implements.implements)
decorator — it returns the function unchanged at runtime and triggers the
structural-subtyping check at the decorator line:

```python
@implements(StrictListSelector[ListAuthorsKwargs, Author])
def list_authors(
    *,
    request: HttpRequest,
    user: UserT,
    **extras: Unpack[ListAuthorsKwargs],
) -> Iterable[Author]: ...
```

The legacy throwaway-variable form still works and is sometimes useful for
ad-hoc one-off checks:

```python
def list_authors(...) -> Iterable[Author]: ...

_check: StrictListSelector[ListAuthorsKwargs, Author] = list_authors
```

A few notes on type-checker support:

- **`ty`** validates `@implements(...)` against the strict Protocols; the
  decorator is the form CI exercises in this repo.
- **`mypy`** rejects `type[Protocol]` arguments under its `type-abstract`
  rule, so `@implements(...)` triggers a `[type-abstract]` error in mypy.
  Either add `# type: ignore[type-abstract]` next to the decorator or stick
  with the `_check: ...` shim form when mypy is your primary checker.
- **PEP 692 support across checkers is uneven** — drift detection on
  `**extras: Unpack[TypedDict]` works best when the function uses the
  `Unpack[...]` form (matching the Protocol). The strict Protocols still
  catch drift on the fixed pool keys (`data`, `instance`, return type) in
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
(the framework can't statically introspect what those provide). The
strict Protocols (above) cover that gap on the static side.
