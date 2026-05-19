# Async

Services and selectors can be `async def`. The dispatcher detects the
coroutine via `inspect.iscoroutinefunction` and routes it correctly:

- under a sync DRF view, it bridges with `asgiref.sync.async_to_sync`
- under an async view, it awaits directly

You don't choose the mode — the callable does.

## When to use it

Reach for async when the service does I/O outside the database:

- HTTP calls to another service
- message-queue publishes
- third-party SDKs that already speak async

```python
import httpx

from rest_framework_services import ServiceCreateView, ServiceSpec


async def fetch_remote(*, request):
    async with httpx.AsyncClient() as client:
        response = await client.get("https://example.com/api/data")
        return response.json()


class FetchView(ServiceCreateView):
    spec = ServiceSpec(service=fetch_remote)
```

## Async ORM

Use the `a*` mutation helpers (`acreate_from_input`,
`aupdate_from_input`) and Django's async ORM (`aget`, `acreate`,
`afilter`, etc.):

```python
from rest_framework_services import aupdate_from_input


async def update_author(*, instance, data):
    result = await aupdate_from_input(instance, data)
    return result.instance
```

`apply_input` is a pure attribute-setter and can be awaited or called
directly — but the helpers that call `save()` need the async variants
to avoid `SynchronousOnlyOperation`.

For the one-line-glue case, the async siblings of the
[default model service factories](default-model-services.md) —
`acreate_model`, `aupdate_model`, `adelete_model` — return an
`async def` closure that wraps `acreate_from_input` /
`aupdate_from_input` / `await instance.adelete()`. The framework's
`is_async` detection routes them through the async dispatch path
automatically.

## Atomic transactions

`atomic=True` (the default) still works. The dispatcher wraps the
async call in `sync_to_async(thread_sensitive=True)` so the ORM
connection stays on a consistent thread. You don't need to do anything
special in the service body — `transaction.atomic()` is applied
transparently.

## Selectors

Async selectors follow the same rules:

```python
async def list_authors(*, request):
    return [author async for author in Author.objects.filter(...).aiterator()]
```

Wire them into `action_specs` exactly like sync selectors — wrapped in
`SelectorSpec`:

```python
action_specs = {
    "list": SelectorSpec(selector=list_authors),
    "retrieve": SelectorSpec(selector=aget_author),
}
```

## Mixing sync and async

You can mix sync and async services in the same viewset / project. The
dispatcher inspects each callable independently — the choice is per
service, not per project. A common pattern is sync-by-default, async
only where I/O dominates.
