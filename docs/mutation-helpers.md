# Mutation helpers

The library doesn't run your services for you, but it does ship the
helpers that DRF's `serializer.save()` quietly performs — minus the
surprises and plus a typed change record.

| Helper | What it does |
|---|---|
| `apply_input(instance, data, ...)` | set attributes in memory; **does not** save |
| `create_from_input(Model, data, ...)` | build, save, optional M2M |
| `update_from_input(instance, data, ...)` | diff in-memory state vs. input, `save(update_fields=[...])` only the fields that actually changed |
| `acreate_from_input` | async sibling of `create_from_input` |
| `aupdate_from_input` | async sibling of `update_from_input` |

The async helpers use Django 4.2+ `asave()` / `aset()`; their kwargs and
return shape are otherwise identical to their sync siblings.

## Shared kwargs

Every helper takes:

- **`data`** — a dataclass instance, plain dict, or any object with
  `__dict__`. The helper iterates the public attributes / keys.
- **`field_map: dict[str, str] | None`** — translate input keys to model
  attribute names. Useful when input contracts diverge from column
  names.
- **`exclude_fields: list[str] | None`** — fields to drop from the input
  before applying. Use it to keep server-controlled columns
  (`created_by`, `updated_at`) out of write paths.

`create_from_input` and `update_from_input` (and their async siblings)
also take:

- **`m2m: dict[str, Any] | None`** — many-to-many assignments applied
  *post-save*. Each value is passed to the manager's `set()`.
- **`children: Mapping[str, ChildSpec] | None`** — reverse-FK child
  collections written from `data[relation]` (create / update / orphan
  reconcile, recursively). See the
  [nested-writes recipe](recipes/nested-writes.md).
- **`context: Mapping[str, Any] | None`** — an **opaque** caller pool
  forwarded into the kwargs of any per-child service a `ChildSpec`
  declares. The helper never reads it; it exists so per-row work
  downstream can see who is calling. The default model services populate
  it for you, so a hand-written service only needs `context=kwargs`.

`update_from_input` (and `aupdate_from_input`) additionally take:

- **`update_fields: bool | list[str]`** (default `True`) — when truthy,
  `save()` is called with `update_fields=<changed columns>` and any
  `auto_now=True` fields are added automatically. Pass `False` for a
  full save, or an explicit list to control exactly which columns are
  written (no auto-injection in that case).

`apply_input` does not save and therefore takes no `m2m` /
`update_fields` kwargs.

## `apply_input` — set attributes, don't save

```python
from rest_framework_services import apply_input


def update_author(*, instance, data):
    apply_input(instance, data, exclude_fields=["created_by"])
    instance.full_clean()
    instance.save()
    return instance
```

Returns the same `ChangeResult` shape as the others — `created` is
always `False`, `changes` is the diff against the in-memory state at
call time.

## `create_from_input`

```python
from rest_framework_services import create_from_input

from myapp.models import Author


def create_author(*, data):
    result = create_from_input(Author, data)
    return result.instance
```

Builds a fresh `Author`, calls `save()`, then applies any `m2m`
assignments. `result.created` is `True`.

## `update_from_input`

```python
from rest_framework_services import update_from_input


def update_author(*, instance, data):
    result = update_from_input(instance, data, exclude_fields=["created_by"])
    if result.get_field_change("email"):
        send_email_changed_notice(instance)
    return result.instance
```

Behaviour worth knowing:

- The diff is computed against the **in-memory** instance state, not
  the database. Reload first if you need to see what's actually stored.
- `save()` runs only when at least one field changed — and is called
  with `update_fields=[changed columns]`, not the whole row.
- `m2m` assignments are applied unconditionally and tracked separately
  in `changes`.

## `ChangeResult` and `FieldChange`

```python
@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any
    new: Any


@dataclass(frozen=True)
class ChangeResult(Generic[ModelT]):
    instance: ModelT
    created: bool
    changes: tuple[FieldChange, ...]

    @property
    def changed_fields(self) -> tuple[str, ...]: ...
    def get_field_change(self, field_name: str) -> FieldChange | None: ...
    def __bool__(self) -> bool: ...  # True iff any change
```

`ChangeResult` is generic over the model type. The mutation helpers
thread that TypeVar through, so `create_from_input(Author, …)` returns
a `ChangeResult[Author]` whose `.instance` is typed as `Author` — no
cast needed at the callsite. The bare name `ChangeResult` (no
parameter) still works and resolves to `ChangeResult[Model]`.

Common patterns:

```python
result = update_from_input(instance, data)

if not result:  # nothing changed
    return result.instance

if "status" in result.changed_fields:
    audit.log("status changed", result.get_field_change("status"))

for change in result.changes:
    metrics.incr(f"author.changed.{change.field}")
```

## `UNSET` — distinguish omitted from `None`

```python
from dataclasses import dataclass

from rest_framework_services import UNSET, UnsetType


@dataclass
class UpdateAuthorInput:
    name: str | None = None
    bio: str | None | UnsetType = UNSET


def update_author(*, instance, data):
    if data.bio is UNSET:
        # client did not send bio — leave whatever's there
        ...
    elif data.bio is None:
        instance.bio = ""
        instance.save(update_fields=["bio"])
    else:
        instance.bio = data.bio
        instance.save(update_fields=["bio"])
```

Annotate the field with `UnsetType` (the type of the `UNSET` singleton) so
the sentinel default type-checks cleanly — no `# type: ignore` needed.

Critical for correct `PATCH` semantics: a missing key and `null` mean
different things. The mutation helpers themselves treat `UNSET` as
"skip this field"; if you use them, you don't have to special-case it
yourself.

```python
update_from_input(instance, data)  # bio==UNSET is skipped automatically
```

## Async siblings

`acreate_from_input` / `aupdate_from_input` exist purely to honour
async ORM calls. The contract is identical:

```python
from rest_framework_services import aupdate_from_input


async def update_author(*, instance, data):
    result = await aupdate_from_input(instance, data)
    return result.instance
```

Use them inside `async def` services. See [Async](async.md).
