# Default model services

When the entire body of your create / update / delete service is a one-line
wrapper over the mutation helpers, the framework ships ready-made factories
so you don't have to write the boilerplate.

## When to use them

Use a default factory when the service body **is** the canonical glue:

```python
def create_author(*, data, **_):
    return create_from_input(Author, data).instance


def update_author(*, instance, data, **_):
    return update_from_input(instance, data).instance


def delete_author(*, instance, **_):
    instance.delete()
```

Keep writing custom services the moment you need anything else:
side-effects, denormalised fields, cross-table updates, calls to other
services, conditional `request.user`-based behaviour, anything that benefits
from a real function name in a stack trace.

## Before / after

Before — hand-written stubs:

```python
from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    create_from_input,
    update_from_input,
)

from myapp.models import Author


def create_author(*, data: AuthorIn, **_):
    return create_from_input(Author, data).instance


def update_author(*, instance: Author, data: AuthorIn, **_):
    return update_from_input(instance, data).instance


def delete_author(*, instance: Author, **_):
    instance.delete()


_author_out = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    output_serializer=AuthorOutSerializer,
)


class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=AuthorInSerializer,
            output_selector_spec=_author_out,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=AuthorInSerializer,
            output_selector_spec=_author_out,
        ),
        "destroy": ServiceSpec(service=delete_author),
    }
```

After — factories:

```python
from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    create_model,
    delete_model,
    update_model,
)

from myapp.models import Author


_author_out = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    output_serializer=AuthorOutSerializer,
)


class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=create_model(Author),
            input_serializer=AuthorInSerializer,
            output_selector_spec=_author_out,
        ),
        "update": ServiceSpec(
            service=update_model(Author),
            input_serializer=AuthorInSerializer,
            output_selector_spec=_author_out,
        ),
        "destroy": ServiceSpec(service=delete_model(Author)),
    }
```

The returned callables conform to the unified
[`CreateService`](reference/services.md) / `UpdateService` / `DeleteService`
Protocols — they accept any framework-pool keys (`request`, `user`, URL
kwargs, `ServiceSpec.kwargs` returns) and ignore the ones they don't need.

## Sync vs async

Each factory ships in two flavours: the sync form (`create_model`,
`update_model`, `delete_model`) returns a regular callable; the async form
(`acreate_model`, `aupdate_model`, `adelete_model`) returns an `async def`
callable. The framework's [`is_async`](async.md) detection routes the
latter through the async dispatch path automatically.

## Configuration

### `field_map` and `exclude_fields`

Forwarded straight to the underlying mutation helper:

```python
create_model(
    Author,
    field_map={"display_name": "name"},
    exclude_fields=["legacy_handle"],
)
```

### `m2m`

Accepts either a static mapping (passed through) or a callable that receives
the validated `data` and returns the mapping. The callable form is the
common case where the M2M values live on the input itself:

```python
@dataclass
class CreatePostIn:
    title: str
    body: str
    tags: list[Tag]


create_model(
    Post,
    exclude_fields=["tags"],
    m2m=lambda data: {"tags": data.tags},
)
```

### `update_fields` (update only)

Same semantics as `update_from_input` — `True` for changed-fields save,
`False` for full save, an explicit list to control exactly which columns
write:

```python
update_model(Post, update_fields=["title", "body"])
```

### `soft_delete` (delete only)

Optional hook that replaces `instance.delete()` — the common archive case:

```python
def _archive(post: Post) -> None:
    post.is_archived = True
    post.save(update_fields=["is_archived"])


delete_model(Post, soft_delete=_archive)
```

The async variant takes an `async def` hook.

## When not to use

If you need `request.user` to stamp `created_by` on the instance, or the
service has to coordinate with another service, write the function out by
hand. The factories cover the boilerplate case; they're not a framework
within the framework.
