# Bulk & collection mutations

Two `ServiceSpec` shapes cover the bulk cases a single-instance spec can't.
They're mutually exclusive.

## `many=True` — a list body in, a list out

Validate the request body as a list, hand the **validated list** to the
service, and render the result list. The service loops itself — one call, one
round-trip where the ORM allows it.

```python
from rest_framework_services import ServiceSpec, SelectorKind, SelectorSpec

@dataclass
class BookIn:
    title: str

def bulk_create_books(*, data: list[BookIn]) -> list[Book]:
    return Book.objects.bulk_create([Book(title=item.title) for item in data])

class BulkCreateBooksView(ServiceCreateView):
    spec = ServiceSpec(
        service=bulk_create_books,
        input_serializer=BookIn,
        many=True,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=BookSerializer
        ),
    )
```

`POST` a JSON array; you get a `201` with the rendered array. Under
`atomic=True` (the default) any item's `ServiceError` rolls the whole batch
back.

## `collection_selector_spec` — operate on a filtered set

The LIST-kind twin of `instance_selector_spec`. It resolves a **scoped set**
(via the selector + [`filter_set`](selector-filtering.md)) and seeds it into the
service as `collection`. Use it for instance-less bulk delete / update — no
single `pk` in the URL.

```python
from rest_framework_services import delete_collection

def published_books(*, user) -> QuerySet[Book]:
    return Book.objects.for_user(user)            # owner-scoped

class BulkDeleteBooksView(ServiceDeleteView):
    spec = ServiceSpec(
        service=delete_collection(Book),          # collection.delete()
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=published_books,
            filter_set=BookFilterSet,             # ?status=draft&… narrows the set
        ),
    )
```

`DELETE /books/?status=draft` deletes the draft set. The filter comes from the
query string; an **empty** set is a harmless no-op (idempotent). A bulk update
is the same shape with a service that calls `collection.update(...)` (return a
summary like `{"updated": n}` to get a `200` body instead of `204`).

`delete_collection` / `adelete_collection` are batteries-included; pass
`soft_delete=lambda qs: qs.update(is_archived=True)` to archive instead.

## Permissions & failures

- **Per-set** — the view / spec `permission_classes` plus the scoped selector
  authorize the action; there is no per-row `check_object_permissions` (a
  per-row opt-in is a planned follow-up).
- **All-or-nothing** — `atomic=True` makes the batch a single transaction.
  Per-item partial-success responses are a tracked follow-up.

A bulk spec runs through the same transport-neutral `dispatch_spec` the MCP
server uses, so the rules are identical on and off HTTP.
