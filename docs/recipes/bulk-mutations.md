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

### Off HTTP: the list as one named argument

A tool call's arguments are always a JSON object, so a caller that only sends
named arguments can never send the bare array an HTTP body is. For that caller
the list travels under one argument instead, `items` unless the spec names
another:

```python
spec = ServiceSpec(
    service=bulk_create_books,
    input_serializer=BookIn,
    many=True,
    many_argument="books",  # optional; the default is "items"
)

schema = spec_to_json_schema(spec, phase="input")
result = dispatch_spec(
    spec, user=user, params={"books": [{"title": "Dune"}]}, many_as_argument=True
)
```

The two halves describe the same input. `spec_to_json_schema` wraps the item
schema in an object with that one required property and nothing else, carrying
the list's own bounds from `allow_empty`, `min_length` and `max_length` as
`minItems` / `maxItems`:

```json
{
  "type": "object",
  "properties": {"books": {"type": "array", "items": {"...": "the BookIn schema"}}},
  "required": ["books"],
  "additionalProperties": false
}
```

`many_as_argument=True` makes `dispatch_spec` read the list out of that
argument. It is a no-op on a single-item spec and on a selector, so a transport
passes it on every call rather than branching on `spec.many`. HTTP never passes
it: a view still validates the request body as the bare array.

Every validation error the list raises comes back keyed under the argument, in
the shape a wrapper serializer declaring `books = BookIn(many=True)` produces on
current DRF. Item errors name only the invalid items, by index, on every DRF
this package supports, where DRF below 3.18 would otherwise report a list with
an empty entry for each valid item:

```json
{"books": {"1": {"title": ["This field may not be blank."]}}}
```

The argument missing, `null`, not a list, empty when the list may not be, or
outside its length bounds answers the way that field would:
`{"books": ["This field is required."]}`, or
`{"books": {"non_field_errors": ["This list may not be empty."]}}`. An argument
sent beside the list is refused as `UnknownArguments.REJECT` refuses one,
whatever `unknown_arguments` says, because the service receives only the list
and the argument would have nowhere to go; the policy still decides what
happens to an undeclared key inside an item. A refusal the service or a
precondition raises is passed on as raised.

A spec that took a list through that wrapper can declare `many=True` instead
and keep its error wire, as long as the wrapper's field was named `items` or
the spec names it with `many_argument`. Below DRF 3.18 the wrapper itself
reported item errors as a list, so there the index mapping is new. Two calls the
wrapper let through are refused: an argument beside the list under any policy
but `REJECT`, and, under `REJECT`, an undeclared key inside an item, which the
wrapper never looked at.

## `collection_selector_spec` — operate on a filtered set

The LIST-kind twin of `instance_selector_spec`. It resolves a **scoped set**
(via the selector + [`filter_set`](selector-filtering.md)) and seeds it into the
service as `collection`. Use it for instance-less bulk delete / update — no
single `pk` in the URL.

```python
from rest_framework_services import delete_collection


def published_books(*, user) -> QuerySet[Book]:
    return Book.objects.for_user(user)  # owner-scoped


class BulkDeleteBooksView(ServiceDeleteView):
    spec = ServiceSpec(
        service=delete_collection(Book),  # collection.delete()
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=published_books,
            filter_set=BookFilterSet,  # ?status=draft&… narrows the set
        ),
    )
```

`DELETE /books/?status=draft` deletes the draft set. The filter comes from the
query string; an **empty** set is a harmless no-op (idempotent). A bulk update
is the same shape with a service that calls `collection.update(...)` (return a
summary like `{"updated": n}` to get a `200` body instead of `204`).

The two channels stay apart here exactly as they do on a single-instance
mutation: the **query string** feeds `filter_set` and the **body** feeds
`input_serializer`. A repeated parameter therefore survives whole, so a
`MultipleChoiceFilter` or an `id__in` filter reads every value of
`?id=1&id=2&id=3`; and a query parameter cannot stand in for a serializer field
it happens to share a name with. Selector *arguments* come from the body and the
route captures — `?status=` reaches `filter_set`, not a `status=` keyword on the
selector.

`delete_collection` / `adelete_collection` are batteries-included; pass
`soft_delete=lambda qs: qs.update(is_archived=True)` to archive instead.

### Return the affected set

By default a collection mutation renders whatever the service returns — a
summary like `{"updated": n}`, or an empty `204`. To render the **set itself**,
add an `output_selector_spec` with `kind=SelectorKind.LIST`: it re-fetches and
renders a list — the output twin of the bulk input. Capture the affected pks
*before* the write, since the collection's own filter may no longer match
afterwards.

```python
def publish_drafts(*, collection: QuerySet[Post]) -> list[int]:
    ids = list(collection.values_list("id", flat=True))
    Post.objects.filter(id__in=ids).update(published=True)
    return ids  # the service result → `result`


def published_by_ids(*, result: list[int]) -> QuerySet[Post]:
    return Post.objects.filter(id__in=result).order_by("id")


class PublishDraftsView(ServiceUpdateView):
    spec = ServiceSpec(
        service=publish_drafts,
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=all_posts,
            filter_set=PostFilterSet,
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,  # ← renders a list, not one row
            selector=published_by_ids,
            output_serializer=PostSerializer,
        ),
    )
```

`PUT /posts/?published=false` publishes the drafts and responds `200` with the
JSON array of the now-published rows. `kind=LIST` on `output_selector_spec` is
valid **only** alongside `collection_selector_spec` — a single-instance
mutation returns one representation (`kind=RETRIEVE`, the default). The re-fetch
runs inside the same transport-neutral `dispatch_spec`, so the MCP server
renders the list identically.

## Permissions & failures

- **Per-set** — the view / spec `permission_classes` plus the scoped selector
  authorize the action; there is no per-row `check_object_permissions` (a
  per-row opt-in is a planned follow-up).
- **All-or-nothing** — `atomic=True` makes the batch a single transaction.
  Per-item partial-success responses are a tracked follow-up.

A bulk spec runs through the same transport-neutral `dispatch_spec` the MCP
server uses, so the rules are identical on and off HTTP.
