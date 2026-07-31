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
| `AdditionalInputRequired` | `rest_framework.exceptions.APIException` | `422` |

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

### `AdditionalInputRequired`

For *"I got far enough to discover I need something else"* — which is not the
same claim as `ServiceValidationError`'s *"what you sent is wrong"*. The
difference that matters is that it is usually **conditional on what the service
found**, so it cannot be expressed as a required field on the serializer:

```python
def delete_rows(*, data):
    doomed = rows_matching(data)
    if len(doomed) > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{len(doomed)} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
```

`schema` describes what is missing, keyed by the input name the service expects
it back under. A transport that can put the question to a human renders it; one
that cannot still has a message worth showing.

⭐ **The answer comes back as ordinary input.** An HTTP client re-submits with
`confirmed` in the body. A transport that asks interactively — MCP, say — merges
the answer into the parameters before dispatch. Either way the service reads it
as a normal argument, which is why raising is the *whole* of the service's
involvement: there is no callback to hold and no session to resume.

It subclasses `ServiceError`, so a transport that has never heard of it still
behaves sensibly. ⚠ A transport that wants to do better must catch it **before**
its generic `ServiceError` handler, or the subclass check will swallow it.

### `ServiceError`

For business-rule violations that **aren't** input errors —
*"the resource exists but is in the wrong state"*. The default mapping
is `422 Unprocessable Entity`, which is exactly what HTTP says about
this case.

```python
raise ServiceError("account is locked")
raise ServiceError("invoice already finalised")
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
Opt out per spec — `atomic` lives on `ServiceSpec`:

```python
class ImportView(ServiceCreateView):
    spec = ServiceSpec(
        service=run_import,
        input_serializer=ImportInput,
        atomic=False,            # the import service handles its own savepoints
    )
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
