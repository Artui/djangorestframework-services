# Nested writes — parent + child collections

The mutation helpers persist a model's scalar columns and its many-to-many
relations. Reverse-FK **child collections** ("one-to-many" — an `Author` with
`books`, an `Order` with `lines`) are written through the `children=` argument,
so you can save a parent and its children from one request **without** binding a
writable-nested serializer and delegating persistence back to `serializer.save()`.

## The shape

`children` maps a relation name to a
[`ChildSpec`](../reference/types.md#childspec). The child rows are read from
`data[relation]`; each child runs back through `create_from_input` /
`update_from_input`, so scalar / m2m / nested semantics compose recursively.

```python
from rest_framework_services import ChildSpec, update_from_input


def update_author(*, instance, data):
    return update_from_input(
        instance,
        data,  # {"name": ..., "books": [{...}, {...}]}
        children={
            "books": ChildSpec(model=Book, fk="author"),
        },
    ).instance
```

- **`fk`** is the child's forward FK field pointing at the parent — set
  automatically on created children.
- **`match_key`** (default `"pk"`) pairs an incoming row with an existing child:
  a row whose key matches updates it, a row with no match is created. Use
  `match_key="id"` when your serializer emits `id` rather than `pk`.
- **`mode`** — `"replace"` (default) reconciles the collection: create new,
  update matched, and **remove orphans** (existing children absent from the
  incoming list). `"merge"` upserts only and never removes.

An orphan is **unlinked** (its FK set to `None`) when the FK is nullable, and
**deleted** otherwise — mirroring `on_delete=SET_NULL` vs `CASCADE`. A relation
the input omits entirely is left untouched; send an explicit `[]` to clear it.

## Grandchildren

A `ChildSpec` can carry its own `children=`, so depth follows the declared tree:

```python
children = {
    "sections": ChildSpec(
        model=Section,
        fk="catalog",
        children={"items": ChildSpec(model=Item, fk="section")},
    ),
}
```

## Declarative — no service body

The default model services forward `children=`, so a parent-with-children
resource needs no hand-written service:

```python
from rest_framework_services import ServiceSpec, create_model

ServiceSpec(
    service=create_model(Author, children={"books": ChildSpec(model=Book, fk="author")}),
    input_serializer=AuthorInput,  # validates name + the books list
)
```

`children` lives on the **helper**, never on `ServiceSpec` — the dispatch
surface stays about transport, not persistence shape.

## Deleting children

`delete_model` accepts `children=` to remove collections **before** the parent
(grandchildren first) — useful when the FK can't cascade for you (a `PROTECT`
relation, or a `soft_delete` Django won't cascade through). Nullable-FK children
are unlinked, the rest deleted, exactly like the orphan rule above:

```python
delete_model(
    Catalog,
    children={"sections": ChildSpec(model=Section, fk="catalog")},
)
```

For a plain hard delete, you usually don't need this — the FK's `on_delete`
already cascades.

## What changed in the response

`ChangeResult.children` carries one
[`ChildCollectionChange`](../reference/types.md#childcollectionchange) per
relation, with the `created` / `updated` / `deleted` / `unlinked` child pks:

```python
result = update_from_input(author, data, children={"books": ChildSpec(model=Book, fk="author")})
delta = result.get_child_change("books")
delta.created, delta.updated, delta.deleted, delta.unlinked
```

## Migrating off a writable-nested serializer

**Before** — persistence lives in the serializer, and the service delegates to it:

```python
class AuthorSerializer(WritableNestedModelSerializer):  # drf-writable-nested / drf-nested
    books = BookSerializer(many=True)


def update_author(*, instance, serializer):
    return serializer.save()  # the coupling we're removing
```

**After** — the serializer only validates; the service owns persistence:

```python
class AuthorSerializer(serializers.ModelSerializer):
    books = BookSerializer(many=True)  # plain nested serializer, validation only

    class Meta:
        model = Author
        fields = ["name", "books"]


def update_author(*, instance, data):
    return update_from_input(
        instance, data, children={"books": ChildSpec(model=Book, fk="author")}
    ).instance
```

Field-level validation stays in the serializer / dataclass; the helper owns the
writes, inside the service's atomic block. The nested serializer no longer needs
a `create()` / `update()` override, and the `drf-writable-nested`-style base
class can go.
