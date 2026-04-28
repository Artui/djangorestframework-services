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
`Unpack[TypedDict]` to pin extras exactly:

```python
from rest_framework_services import StrictCreateService
from typing import TypedDict

class CreateAuthorKwargs(TypedDict):
    tenant_id: int

def create_author(
    *,
    data: AuthorIn,
    request: HttpRequest,
    user: UserT,
    tenant_id: int,        # must match CreateAuthorKwargs
) -> Author: ...

# Drift now produces a type error:
_check: StrictCreateService[AuthorIn, Author, CreateAuthorKwargs] = create_author
```

Available strict Protocols:

- `StrictCreateService[InputT, ResultT, ExtraT]`
- `StrictUpdateService[InputT, InstanceT, ResultT, ExtraT]`
- `StrictDeleteService[InstanceT, ResultT, ExtraT]`
- `StrictListSelector[ResultT, ExtraT]`
- `StrictRetrieveSelector[ResultT, ExtraT]`
- `StrictOutputSelector[InT, OutT, ExtraT]`

The strict and lenient variants are interchangeable at the
`ServiceSpec.service` field — pick the level of enforcement per service.

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
