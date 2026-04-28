# Errors & atomic

Two ground rules:

1. **Services don't import from DRF.** They raise framework-agnostic
   exceptions; the view boundary translates them.
2. **Every service call runs inside `transaction.atomic()` by default.**
   Opt out per `ServiceSpec` (or per view) when the service manages its
   own transaction boundaries.

## Framework-agnostic exceptions

```python
from rest_framework_services import ServiceError, ServiceValidationError


def withdraw(*, instance, data):
    if data.amount > instance.balance:
        raise ServiceValidationError({"amount": ["insufficient funds"]})
    if instance.locked:
        raise ServiceError("account is locked")
    instance.balance -= data.amount
    instance.save(update_fields=["balance"])
    return instance
```

The view maps them to DRF responses:

| Raised | Becomes | HTTP |
|---|---|---|
| `ServiceValidationError` | `rest_framework.exceptions.ValidationError` | `400` |
| `ServiceError` | `rest_framework.exceptions.APIException` | `422` |

### `ServiceValidationError`

For business-rule violations that should look like input validation
errors to the client. Accepts the same shapes DRF's `ValidationError`
does:

```python
raise ServiceValidationError("bad input")
raise ServiceValidationError(["error 1", "error 2"])
raise ServiceValidationError({"field": ["per-field error"]})
raise ServiceValidationError({"non_field_errors": ["whole-form error"]})
```

The translated DRF response is a normal `400` with the same body shape
your serializers produce. From the client's point of view, a service
violation and a serializer violation are indistinguishable — which is
usually what you want.

### `ServiceError`

For business-rule violations that **aren't** input errors —
*"the resource exists but is in the wrong state"*. The default mapping
is `422 Unprocessable Entity`, which is exactly what HTTP says about
this case.

```python
raise ServiceError("account is locked")
raise ServiceError("invoice already finalised", code="already_finalised")
```

### Why not just raise DRF exceptions?

Because the mutation can be reused outside a request — from a
management command, a background job, a test. Coupling business rules
to HTTP status codes makes those reuses awkward; coupling them to
*what kind of error happened* keeps them portable.

The boundary translation costs you nothing inside a view; outside a
view, you catch `ServiceError` / `ServiceValidationError` directly.

## Atomic transactions

By default every service call is wrapped in `transaction.atomic()`.
Opt out per spec:

```python
ServiceSpec(
    service=run_import,
    input_serializer=ImportInput,
    atomic=False,                # the import service handles its own savepoints
)
```

Or per view (the spec's `atomic` value still wins if both are set):

```python
class ImportView(ServiceCreateView):
    spec = ServiceSpec(service=run_import, input_serializer=ImportInput)
    atomic = False
```

When to opt out:

- the service drives a long-running import that needs per-row
  savepoints,
- the service must commit partial work before raising,
- the service spans systems and the database side is intentionally
  best-effort.

When **not** to opt out: the service makes more than one related write
and you'd want them to roll back together on failure. That's the case
the default exists for.

## Async + atomic

Atomic wrapping works for `async def` services too. The dispatcher
wraps the call in `sync_to_async(thread_sensitive=True)` so the ORM
connection stays on a consistent thread. You don't need to do anything
special in the service body.

See [Async](async.md).
