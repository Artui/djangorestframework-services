# Pass extra kwargs to services

The dispatch flow assembles a kwarg pool — `data`, `instance`,
`request`, `user`, `view` — and the callable receives the subset it
declares. To add something to that pool, override
`get_service_kwargs()` (mutation views) or `get_selector_kwargs()`
(selector views).

## Why not just read it from `request`?

You can. But:

- the service signature stops being a contract — a reader can't tell
  what the service depends on without reading its body,
- tests have to construct a request to exercise the service.

Passing the dependency in via the kwarg pool means the service's
parameter list documents what it needs, and tests can call the service
directly with plain kwargs.

## Add a tenant kwarg

```python
class TenantedAuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    service_specs = {
        "list": list_authors,
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorOutputSerializer,
        ),
    }

    def get_service_kwargs(self):
        return {**super().get_service_kwargs(), "tenant": self.request.tenant}

    def get_selector_kwargs(self):
        return {**super().get_selector_kwargs(), "tenant": self.request.tenant}
```

```python
def create_author(*, data, tenant):
    return Author.objects.create(name=data.name, tenant=tenant)


def list_authors(*, tenant):
    return Author.objects.filter(tenant=tenant)
```

A service that doesn't declare `tenant` simply won't get it. Adding the
kwarg to the pool is non-breaking.

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

## Per-spec extras with `**kwargs`

A service that declares `**kwargs` receives the entire pool and can
pull out whatever it needs:

```python
def create_author(*, data, **kwargs):
    tenant = kwargs.get("tenant")
    request = kwargs.get("request")
    ...
```

Useful for services that span actions or are wrapped in adapters
(e.g. by `djangorestframework-mcp`). Avoid it on first-party code —
explicit kwargs are easier to grep for.

## `data` is special

You don't add `data` via `get_service_kwargs()` — it comes from the
spec's `input_serializer` and is set by the mutation flow. Anything
the user supplied lives there. Add server-side context (tenant, clock,
feature flag) through `get_service_kwargs()` instead.
