# Pass extra kwargs to services

The dispatch flow assembles a kwarg pool — `request`, `user`, `data`
(mutations with an `input_serializer`), `instance` (update / destroy),
URL kwargs (selectors), plus any extras you wire in — and the callable
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

The per-spec callable is called once per call, from `view` and `request`, and a
key its `TypedDict` return annotation fills is never taken from the client or a
hook. A key a view hook returns that names a pool seed (`user`, `data`,
`instance` and the rest) is dropped, and the seed wins. The per-spec callable
returning such a key raises `ImproperlyConfigured` instead: any of `request`,
`view`, `user`, `data`, `instance`, `collection`, `result`, `queryset` and
`progress`, or a name registered in `pool_seeds`. Rename the key, and the
parameter that reads it; to hand over the principal, pass `user=` to
`dispatch_spec`, or let the view's request carry it.

**A key naming an input-serializer field is that field's value.** Where a hook
or the spec's `kwargs` returns a key that matches a field of `input_serializer`,
by its name or by its `source`, the value is written into the field, over
whatever the client sent, and validated as the field validates. A hook returning
`{"tenant": request.user.tenant}` therefore decides the `tenant` the service
receives in `data`; a client cannot spoof it by putting `tenant` in the body.
Nor by a second route to the same key. Once the serializer has accepted the body,
the value the hook gave is validated on its own, by the field and its
`validate_<field>`, never what the field reads back from the body. What that gives
is handed to the service's parameter named by the field's `source`, and by the key
the hook used where that differs, and is laid back under that key in `data`. So a
field that reads more of the body than its own key (a nested serializer or a dict
reading a form body's `tenant.id`, a field whose `get_value` reads another key),
a later field with the same `source`, a
`source="*"` nested serializer, a `validate()` writing `tenant` from the client's
input, a `validate()` or another field's `validate_<field>` running `tenant`'s
validation on a value of its own, and a `to_internal_value` override that writes
the client's value over `tenant` in the body or builds `tenant` without the
field, do not replace it. A refusal of the value is a 400 keyed by the field, as
DRF's field loop answers one. A field with a dotted `source` or `source="*"`
has no key of its own in `data`, so its value reaches the parameter named like
the field.

Two shapes keep the parameter and `data` apart. A dataclass input's `data` is
the instance as the serializer built it, so a `validate()` that rewrites the
instance's attribute is seen there and not by the parameter. A `many` spec's
rows are handed on as its list serializer built them.

That validation is separate from the serializer's own pass, which costs where a
field does work. A related field looks its value up once more, one more query
for a `PrimaryKeyRelatedField`, and the field's validators and its
`validate_<field>` run twice, as do a nested serializer's `validate()` and its own
fields' validators and `validate_<field>`, so a validator that queries, a
`UniqueValidator`, queries twice, and one with side effects has them twice.

On a form body, multipart or urlencoded, the value is read as DRF reads a form: an
empty string for an optional field leaves the field out, or is `None` where the
field allows null, and one value for a list field or a `MultipleChoiceField` is a
list of one. A `JSONField`'s mapping is refused on a form body, where a JSON body
takes it, so for that field the answer depends on the client's content type.

Validated as the field validates means the value is given in the form a client
would send it. A related field takes a primary key, and the service receives
the row; a model instance is refused with a 400. A value the field's validators
refuse is a 400 for the client. Where the server owns the value outright, make
the field read-only or leave it off the serializer: the value then reaches the
service parameter of its name as given.

> `view` is seeded beside `request`: a service or selector declaring it is
> handed the calling view; a hook's `view` key is dropped, and the per-spec
> callable returning one is refused. Services and selectors are still best kept plain business logic;
> pipe the view state they need through one of these hooks (the per-spec
> callable receives a narrow `ServiceView` if it needs URL kwargs or `action`).

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
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorSerializer,
        ),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=AuthorSerializer,
            ),
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

Pair with the parameterised service Protocols (`CreateService`,
`UpdateService`, …) and the `@implements(...)` decorator. The kwargs
shape is typed on your function (`**extras: Unpack[YourKw]`); the
Protocol checks the surrounding shape (`data`, `instance`, return type):

```python
from typing_extensions import Unpack, TypedDict

from rest_framework_services import UpdateService, implements


class PublishAuthorKwargs(TypedDict, total=False):
    author_id: int
    actor_id: int


@implements(UpdateService[AuthorIn, Author, Author])
def publish_author(
    *,
    instance: Author,
    data: AuthorIn,
    **extras: Unpack[PublishAuthorKwargs],
) -> Author: ...
```

`total=False` (or per-field `NotRequired`) keeps the function
Protocol-conformant. If `publish_author` needs `request` or `user`,
subclass [`HttpExtras`](../typing.md) instead — it is itself declared
`total=False`.

See [Typing services and selectors](../typing.md) for the full Protocol
catalogue and notes on type-checker support.

### What the return annotation declares

The provider's return annotation is read before it runs, by
[`provider_keys`][rest_framework_services.dispatch.provider_keys.provider_keys].
That is how a transport serving a selector off HTTP, such as an MCP tool or an
agent toolset, knows which names not to ask its caller for. Its answer is a
[`ProviderKeys`][rest_framework_services.types.provider_keys.ProviderKeys], with
`filled` and `declinable` sets of key names that also unpack as a pair. It
reads three things:

- **Every key of the `TypedDict` is one the provider fills**, optional keys
  included, since the provider owns them. These are `filled`.
- **A key whose value admits `UnsetType` is one it may decline**, and moves
  from `filled` to `declinable`. Return `UNSET` for it and dispatch drops the
  key from the pool, so the caller's value is the one the callable receives.
  Only a union counts: `tenant: str | UnsetType` may be declined, while
  `regions: list[str | UnsetType]` is always filled with a list.
- **A provider whose return says nothing may fill any name**: one with no
  return annotation, a plain `dict`, a lambda, or a return annotation that does
  not resolve. The answer is `None` rather than a `ProviderKeys`.

Each annotation costs only what it says. A parameter typed with a name imported
only under `if TYPE_CHECKING:`, where a linter's type-checking rules move it,
leaves the keys readable. A `TypedDict` value that does not resolve makes only
its own key one the provider may decline. A generic `TypedDict` is read with
whatever binds its parameters, so `-> Scope[str | UnsetType]` reads `Scope`'s
`tenant: T` as the written-out `tenant: str | UnsetType`. So does a subclass
that binds them, `class Declining(Scope[str | UnsetType])`, and one that hands
its own parameter on, `class Relay(Scope[U], Generic[U])` returned as
`Relay[str | UnsetType]`. An argument wrapped in `Annotated` reads as what it
wraps, in a class statement's bases as in the return annotation. A PEP 695
`class Scope[T](TypedDict)` reads the same under
`from __future__ import annotations`.

Each key is read with the bindings of the class that declared it, so one `T`
reused at every level reads as each class binds it.
`class Shadow(Scope[list[T]], Generic[T])` with a key `extra: T` of its own,
returned as `Shadow[str | UnsetType]`, fills `Scope`'s `tenant` with a list
and may decline `extra`. A key reached through two bases that bind it
differently is filled if either reading fills it. Three things are not read:
a PEP 696 default on a type variable; a key a subclass redeclares, which reads
as its base declared it, because a `TypedDict` keeps no record of which class
wrote a key; and a base whose arguments cannot be written in the return
annotation's terms, which leaves the provider untyped.

A type variable nothing binds, as in a bare `-> Scope`, counts as filled.
Whether it may be `UNSET` is written nowhere, and filled keeps the decision with
the provider by hiding the key from the caller. Declinable would let a transport
whose caller's value wins put that value in a key the provider was meant to
decide, such as a tenant.

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
from dataclasses import dataclass
from typing import Any
from rest_framework.request import Request
from rest_framework_services import ServiceSpec, ServiceView


@dataclass
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
`DeleteService[InputT, InstanceT, ResultT]` and bind `InputT` to your
input dataclass. For services that don't read a body, bind `InputT` to
the [`NoInput`](../reference/types.md#noinput) sentinel and don't declare
`data` on the function:

```python
from dataclasses import dataclass

from typing_extensions import Unpack

from rest_framework_services import (
    DeleteService,
    HttpExtras,
    ServiceSpec,
    implements,
)


@dataclass
class DeleteReasonIn:
    reason: str


@implements(DeleteService[DeleteReasonIn, Author, None])
def delete_author(
    *,
    instance: Author,
    data: DeleteReasonIn,
    **extras: Unpack[HttpExtras[MyUser]],
) -> None:
    user = extras.get("user")
    AuditLog.objects.create(actor=user, target=instance, reason=data.reason)
    instance.delete()


# In the view / viewset:
ServiceSpec(service=delete_author, input_serializer=DeleteReasonIn)
```

The OpenAPI schema (when `enable_openapi()` is in effect) will include a
`requestBody` for the DELETE endpoint matching the input serializer.
