# Pass extra kwargs to services

The dispatch flow assembles a kwarg pool — `data`, `instance`, `request`,
`user`, plus URL kwargs and any extras you wire in — and the callable
receives the subset it declares. There are three places to inject extras,
applied in order of increasing specificity (later wins on overlapping keys):

1. **Catch-all hook** — `get_service_kwargs()` /
   `get_selector_kwargs()` on the view. Global default for every action.
2. **Per-action hook** — `get_<action>_service_kwargs()` /
   `get_<action>_selector_kwargs()`. Keeps multi-action viewsets free of
   `if self.action == ...` branches.
3. **Per-spec callable** — `ServiceSpec.kwargs` / `SelectorSpec.kwargs`.
   Co-located with the service it feeds.

Use whichever level matches the granularity of your contract.

> `view` is **not** in the pool. Services and selectors are plain business
> logic; pipe view state through one of these hooks (the per-spec callable
> receives a narrow `ServiceView` if it needs URL kwargs or `action`).

## Why not just read it from `request`?

You can. But:

- the service signature stops being a contract — a reader can't tell
  what the service depends on without reading its body,
- tests have to construct a request to exercise the service.

Passing the dependency in via the kwarg pool means the service's
parameter list documents what it needs, and tests can call the service
directly with plain kwargs.

## Catch-all: a tenant kwarg for every action

```python
class TenantedAuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(selector=list_authors, output_serializer=AuthorSerializer),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorSerializer,
        ),
    }

    def get_service_kwargs(self):
        return {**super().get_service_kwargs(), "tenant": self.request.tenant}

    def get_selector_kwargs(self):
        return {**super().get_selector_kwargs(), "tenant": self.request.tenant}
```

A service that doesn't declare `tenant` simply won't get it. Adding the
kwarg to the pool is non-breaking.

## Per-action: split the contract by method name

When different actions need different extras, name the hook after the
action — no branching:

```python
class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(service=create_author, input_serializer=CreateAuthorInput),
        "publish": ServiceSpec(service=publish_author),
    }

    def get_create_service_kwargs(self):
        return {"tenant_id": self.request.tenant.id}

    def get_publish_service_kwargs(self):
        return {"actor_id": self.request.user.pk}
```

## Per-spec: contract co-located with the service

For the strictest contract, attach the kwargs callable directly to the
spec. The provider receives the view (typed as the narrow `ServiceView`)
and the request, and returns a `TypedDict` describing the keys it adds:

```python
from typing import TypedDict
from rest_framework_services import ServiceView

class PublishAuthorKwargs(TypedDict):
    author_id: int
    actor_id: int

def _publish_author_kwargs(view: ServiceView, request) -> PublishAuthorKwargs:
    return {
        "author_id": int(view.kwargs["pk"]),
        "actor_id": request.user.pk,
    }

class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "publish": ServiceSpec(
            service=publish_author,
            kwargs=_publish_author_kwargs,
        ),
    }
```

Pair with `StrictCreateService` / `StrictUpdateService` (etc.) and the
`@implements(...)` decorator to have type checkers enforce that the service
signature matches the `TypedDict` exactly:

```python
from rest_framework_services import StrictUpdateService, implements

@implements(StrictUpdateService[AuthorIn, Author, PublishAuthorKwargs, Author])
def publish_author(
    *,
    instance: Author,
    data: AuthorIn,
    request,
    user,
    **extras: Unpack[PublishAuthorKwargs],
) -> Author: ...
```

See [Typing services and selectors](../typing.md) for the full Protocol
catalogue and notes on type-checker support.

## Add a clock for tests

```python
from datetime import datetime


class InvoiceViewSet(ServiceViewSet):
    def get_service_kwargs(self):
        return {**super().get_service_kwargs(), "now": datetime.now}
```

```python
def issue_invoice(*, data, now):
    invoice = Invoice.objects.create(
        customer=data.customer,
        issued_at=now(),
    )
    return invoice
```

In tests, swap the clock without touching the request:

```python
def test_issue_invoice(monkeypatch):
    fixed_now = lambda: datetime(2026, 1, 1)
    invoice = issue_invoice(data=CreateInvoiceInput(...), now=fixed_now)
    assert invoice.issued_at == datetime(2026, 1, 1)
```

## `data` is special

You don't add `data` via the kwargs sources — it comes from the spec's
`input_serializer` and is set by the mutation flow. Anything the user
supplied lives there. Add server-side context (tenant, clock, feature
flag) through one of the three hooks instead.

If you need a *path kwarg* (e.g. a parent ID from a nested route) inside
the serializer's `validated_data` — typically because the serializer
validates a cross-field invariant against it — use the symmetrical
`input_data` chain. Same three layers, same precedence, but the result
is merged on top of `request.data` *before* the serializer is
instantiated:

```python
from typing import Any
from rest_framework.request import Request
from rest_framework_services import ServiceSpec, ServiceView


class CreateChildIn:
    name: str
    parent_id: int  # required field, but server-provided


def _create_child_input(view: ServiceView, request: Request) -> dict[str, Any]:
    return {"parent_id": int(view.kwargs["parent_id"])}


class ChildViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(
            service=create_child,
            input_serializer=CreateChildIn,
            input_data=_create_child_input,
        ),
    }
```

Server-provided values win on key conflict, so a malicious client can't
rebind `parent_id` by including it in the body. Catch-all
`get_input_data(request)` and per-action `get_<action>_input_data(request)`
are also available on the view for the same shape.

## Delete with a payload

`DELETE` services can accept body data — e.g. a deletion reason — by
setting `input_serializer` on the spec. Type the service against
`StrictDeleteService[InputT, InstanceT, ExtraT, ResultT]` and bind
`InputT` to your input dataclass. For services that don't read a body,
bind `InputT` to the `NoInput` sentinel and don't declare `data` on the
function:

```python
from typing import Unpack
from rest_framework_services import (
    NoKwargs,
    NoInput,
    ServiceSpec,
    StrictDeleteService,
    implements,
)


@dataclass
class DeleteReasonIn:
    reason: str


@implements(StrictDeleteService[DeleteReasonIn, Author, NoKwargs, None])
def delete_author(
    *,
    instance: Author,
    data: DeleteReasonIn,
    request,
    user,
    **extras: Unpack[NoKwargs],
) -> None:
    AuditLog.objects.create(actor=user, target=instance, reason=data.reason)
    instance.delete()


# In the view / viewset:
ServiceSpec(service=delete_author, input_serializer=DeleteReasonIn)
```

The OpenAPI schema (when `enable_openapi()` is in effect) will include a
`requestBody` for the DELETE endpoint matching the input serializer.
