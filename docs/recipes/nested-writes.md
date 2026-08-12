# Nested writes — every relation kind

The mutation helpers persist a model's scalar columns. They also persist its
**relations**, from the same payload and in one call, so you can save a parent
and everything hanging off it from one request **without** binding a
writable-nested serializer and delegating persistence back to
`serializer.save()`.

Every relation kind Django has is covered — forward FK and one-to-one, reverse
FK collections, reverse one-to-one, many-to-many, and generic relations — with
one exception named in [What is not covered](#what-is-not-covered).

## The shape

`relations` maps a relation name to the spec for its kind. The nested payload is
read from `data[relation]`; each row runs back through `create_from_input` /
`update_from_input`, so scalar / m2m / nested semantics compose recursively.

```python
from rest_framework_services import ChildSpec, ForwardRelationSpec, update_from_input


def update_post(*, instance, data):
    return update_from_input(
        instance,
        data,  # {"title": ..., "author": {...}, "comments": [{...}]}
        relations={
            "author": ForwardRelationSpec(model=Author, scope=Author.objects.all()),
            "comments": ChildSpec(model=Comment, fk="post"),
        },
    ).instance
```

| Relation | Spec | Written |
|---|---|---|
| Forward FK / one-to-one (`Post.author`) | [`ForwardRelationSpec`](../reference/types.md#forwardrelationspec) | before the parent's `save()` |
| Reverse FK collection (`Author.posts`) | [`ChildSpec`](../reference/types.md#childspec) | after |
| Reverse one-to-one (`Author.profile`) | [`ReverseOneToOneSpec`](../reference/types.md#reverseonetoonespec) | after |
| Generic relation (`Catalog.attachments`) | [`GenericRelationSpec`](../reference/types.md#genericrelationspec) | after |
| Many-to-many (`Post.tags`, either side) | [`ManyToManySpec`](../reference/types.md#manytomanyspec) | last |

**The order comes off the spec class, never off the map.** A forward foreign key
has to exist before the parent is saved and a many-to-many has to be linked
after; that is a property of the kind, not of how you happened to spell the
dict. Each class declares its
[`RelationPhase`](../reference/types.md#relationphase) and the driver walks the
phases in order. Declaration order still decides everything the phases leave
open: two relations in the same phase are written in the order you declared
them.

Writing the forward pair first is not only correctness. By the time the parent
is built or diffed, the relation is an ordinary column value, so it flows
through the same `diff_attrs` reporting and the same minimal `update_fields`
save as any other field, with nothing added for the occasion.

`children=` is the reverse-FK alias — the same map under the name it shipped as
— so nothing written against it needs changing. A name declared in both raises.

## Reverse-FK child collections

```python
children = {"books": ChildSpec(model=Book, fk="author")}
```

- **`fk`** is the child's forward FK field pointing at the parent — set
  automatically on created children.
- **`match_key`** (default `"pk"`) pairs an incoming row with an existing child:
  a row whose key matches updates it, a row with no match is created. Use
  `match_key="id"` when your serializer emits `id` rather than `pk`.
- **`mode`** — `"replace"` (default) reconciles the collection: create new,
  update matched, and **remove orphans** (existing children absent from the
  incoming list). `"merge"` upserts only and never removes.
- **`orphan`** — what removing one *does*, where `mode` says whether one is
  removed at all. See below.

A relation the input omits entirely is left untouched; send an explicit `[]` to
clear it.

### What happens to an orphan

By default (`orphan="auto"`) it is **unlinked** — its FK set to `None` — when
the FK is nullable, and **deleted** otherwise, mirroring `on_delete=SET_NULL`
versus `CASCADE`. That honours what the model already declares, which is why it
is the default rather than `drf-nested`'s unconditional delete.

`orphan="unlink"` and `orphan="delete"` say it outright instead:

```python
ChildSpec(model=Book, fk="author", orphan="delete")  # gone, nullable FK or not
```

Reach for one when the spec *means* a particular disposal. Nullability is a fact
about a column, not a statement of intent: someone adding `null=True` in a later
migration flips a `"replace"` from destructive to non-destructive, with no change
to the spec and none to its tests, and the rows it quietly stops removing pile up
where nobody is looking.

`orphan="unlink"` against a link that cannot hold `NULL` raises
`ImproperlyConfigured` when the relation is written, naming the relation and the
column — there is nothing to blank, and deleting the row instead would be the
opposite of what was asked. The check is at write time because a spec is
routinely built at import time, before Django can be asked about a column at all.

The flag is on the kinds that own their rows —
[`ChildSpec`](../reference/types.md#childspec),
[`GenericRelationSpec`](../reference/types.md#genericrelationspec),
[`ReverseOneToOneSpec`](../reference/types.md#reverseonetoonespec) — and on no
others: a many-to-many target is shared and never deleted, and a forward
relation removes nothing. It governs the [`delete_model` cascade](#deleting)
too, which disposes of the same rows. `delete_service` *is* the disposal, so
declaring an explicit `orphan` beside one raises at construction.

Matching happens inside the parent's own manager, so a child collection needs no
`scope=`: a row the parent doesn't own is not reachable to begin with.

### Grandchildren

A spec can carry its own `children=` / `relations=`, so depth follows the
declared tree:

```python
children = {
    "sections": ChildSpec(
        model=Section,
        fk="catalog",
        children={"items": ChildSpec(model=Item, fk="section")},
    ),
}
```

## Forward FK and one-to-one

One spec covers both, because Django does: `OneToOneField` subclasses
`ForeignKey`, and the column being unique changes nothing about how it is
written.

```python
relations = {"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())}
```

The value at `data["author"]` reads three ways:

- **omitted** — untouched.
- **`None`** — the parent's foreign key is set to `None`. The row it pointed at
  is *not* removed: a forward target is not owned by the parent and may be
  shared with other rows. Removing rows is the reverse kinds' job.
- **a mapping** — the target row is written. Without a `match_key` it is
  created; with one it names a row, matched against [`scope`](#scope).

A `match_key` that matches nothing in scope raises a 400 rather than falling
through to a create — see [scope](#scope) for why. To merely point the column at
a row that already exists, don't declare a relation at all: pass the pk or the
instance as the plain field it is.

## Reverse one-to-one

The other side of a `OneToOneField` — the column lives on the related row and
the parent reaches at most one of them:

```python
relations = {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}
```

- **omitted** — untouched.
- **`None`** — the existing row, if any, is removed by the [orphan
  rule](#what-happens-to-an-orphan): by default **unlinked** when `fk` is
  nullable and **deleted** when it is not, or whichever `orphan=` states. Unlike
  a forward relation, this row *is* the parent's, so clearing the relation has
  to do something about it.
- **a mapping** — updated when the parent already has a row, created and linked
  when it does not.

No `match_key` and no `scope`: the parent owns at most one row here, so the
relation itself is the match.

## Many-to-many written from the payload

```python
relations = {"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())}
```

Each row in `data["tags"]` is a **payload**, not a key: the target row is
created or updated first, and only then is the membership written. Forward and
reverse are one code path — Django hands back the same related manager whether
you name the field (`Post.tags`) or the reverse accessor (`Tag.posts`).

- **`mode`** — `"replace"` (default) makes the incoming set authoritative and
  drops the members it leaves out; `"merge"` adds and never drops.
- A dropped target is **unlinked, never deleted**. The row is shared with every
  other parent linked to it, so the only thing the loop removes is the
  membership.

Matching is done in `scope`, never in the parent's current membership: the
payload names the rows to link, which is exactly the set that is not linked yet.

### `m2m=` still exists, and is a different job

The helpers' `m2m=` keyword **assigns rows that already exist** (pks or
instances) and creates nothing:

```python
create_from_input(Post, data, m2m={"tags": [tag_a, tag_b]})
```

Both ship. What they cannot do is share a relation — they would both write it,
in an order you did not choose, and only the second would survive. **A relation
named by `m2m=` and by a `ManyToManySpec` raises `ImproperlyConfigured`**;
assign it or write it, not both. Different relations on the same call are fine.

## Generic relations

A reverse-FK collection with the foreign key replaced by two columns — a
`ForeignKey` to `ContentType` saying which model the row belongs to, and an id
column saying which row:

```python
relations = {"attachments": GenericRelationSpec(model=Attachment)}
```

Both are injected from the saved parent. Everything else is the child-collection
loop: matched inside the parent's own accessor (so no `scope=`), `mode`,
`orphan`, grandchildren, services. By default an orphan is **unlinked** when
*both* link columns are nullable and **deleted** otherwise — the same rule
applied to a pair, because half a severed link is a row pointing at a content
type with no row id — and `orphan=` overrides it exactly as [it does for a child
collection](#what-happens-to-an-orphan).

Set `content_type_field` / `object_id_field` when the model spells the columns
differently; they mirror the arguments of the same name on Django's
`GenericRelation`.

!!! note "This kind needs `django.contrib.contenttypes` in `INSTALLED_APPS`"

    It is the library's only contact with that app, and the app is not
    guaranteed to be installed, so the content-type lookup is resolved on first
    use rather than imported with the package — `rest_framework_services` itself
    ships in `INSTALLED_APPS` and is imported while Django is still populating
    the app registry, where importing a model raises `AppRegistryNotReady`.

    Declaring a `GenericRelationSpec` is always safe. *Writing* one without the
    app installed raises `ImproperlyConfigured` naming the app and the remedy.
    There is nothing extra to `pip install`: contenttypes ships inside Django,
    so the opt-in is the `INSTALLED_APPS` entry.

## `scope`

`scope` names the rows this caller is allowed to write. It is a queryset, or a
callable resolved from the caller pool by signature — the library's usual idiom:

```python
ForwardRelationSpec(model=Author, scope=lambda user: Author.objects.filter(owner=user))
```

**Only the two unowned kinds take it** — forward relations and many-to-many —
and the reason is the interesting part, not the syntax.

A child collection, a reverse one-to-one and a generic relation are all reached
*through the parent*: matching happens inside a manager the parent owns, so a
row belonging to somebody else is not reachable at all. A forward target and a
many-to-many target have no such manager. They are shared by definition — every
row pointing at them reaches the same row — so matching one by key with nothing
to constrain it means **"any caller may write any row of that model by guessing
a key"**. That is not a hypothetical: a payload of `{"pk": 7, "name": "..."}`
against an unscoped spec would fetch row 7 and write the caller's values onto
it.

So:

- **No `scope`** ⇒ the spec is **create-only**. A payload carrying a `match_key`
  raises `ImproperlyConfigured` rather than quietly creating a duplicate. It is
  a misconfiguration and not a 400 because the remedy is always "declare a
  scope" and never "send different data" — a 4xx would give the client advice it
  cannot act on.
- **With `scope`**, a `match_key` that matches nothing in it raises
  `ServiceValidationError` (a 400) rather than falling through to a create. A
  `match_key` on an unowned target *identifies* a row — "point at this one" — so
  there is nothing sensible to create in its place. Creating one anyway would be
  unsafe as well as surprising: the key travels in the payload, so a `pk` naming
  an out-of-scope row would be written straight back onto that row by
  `Model.save()`, reaching the very row the scope exists to protect.

That last sentence is also why a *child collection* behaves differently: an
unmatched natural key there really does mean "a new child", because the match
ran inside the parent's own manager.

## A nested create may not carry a primary key

**`Model(pk=7, ...).save()` is an UPDATE.** So a nested payload that reaches the
create branch while still carrying a primary key does not create anything — it
reassigns and overwrites row 7, whoever owns it. Scoping the *match* does not
constrain the *write* that follows it.

Every kind funnels through one create, and that create refuses a payload naming
a primary key the match did not resolve, with `ServiceValidationError`:

```python
# Refused: pk 42 is not in this author's collection.
update_from_input(author, {"books": [{"pk": 42, "title": "..."}]}, children=BOOKS)
```

The guard runs **before** a declared `create_service` is dispatched, so your own
code is never handed the key either. Raising rather than stripping it is
deliberate: the caller named a specific row, and quietly creating a different one
does the opposite of what was asked and hands back a pk they never chose.

A **non-primary** `match_key` — a natural key like an ISBN or a slug — is
untouched by this, so declaring one is how you get upsert semantics.

## When a row's write has behaviour of its own

A row that needs side effects, derived columns, an event or an external call
doesn't fit a plain helper call. Rather than abandon the facility and hand-write
the reconciliation loop, put a service in the slot for that operation:

```python
ChildSpec(
    model=Book,
    fk="author",
    create_service=publish_book,  # (*, data, parent, **extras) -> Book
    update_service=revise_book,  # (*, instance, data, parent, **extras) -> Book | None
    delete_service=archive_book,  # (*, instance, parent, **extras) -> None
)
```

**The spec owns reconciliation; the service owns the row.** Matching, `mode`
and orphan handling never move into your code — a slot is called once per row
the loop has already decided about.

- `data` reaches a create service with the link to the parent already set (the
  `fk`, or both generic columns): linking the row is reconciliation, not row
  behaviour. A `ForwardRelationSpec` create service gets no `parent` at all,
  since the parent does not exist yet, and a `ManyToManySpec` one gets `parent`
  but no link in `data` — the link is a through row the loop writes afterwards.
- An update service returning `None` means "use the in-memory instance", the
  same convention the top-level update services follow.
- A delete service replaces the unlink-or-delete rule for that row (orphan
  removal *and* the `delete_model` cascade). The loop can no longer tell the
  two apart, so the pk is reported under `removed` — its own bucket, rather
  than a guess folded into `deleted`. `ManyToManySpec` has no delete slot: it
  never deletes a target, only the membership.
- A `create_service` / `update_service` **replaces** the helper call, so the
  knobs configuring that call — `field_map`, `exclude_fields`, `m2m` and the
  nested `children` / `relations` maps — are refused on the same spec, with
  `ImproperlyConfigured` at construction. They would otherwise be dead
  configuration, and a spec declaring both a `create_service` and `children=`
  would write no grandchildren while saying nothing. `delete_service` is exempt:
  it replaces the removal rule rather than the helper call, so the cascade still
  removes a row's grandchildren before handing the row over.
- Each service receives only the pool keys it declares. Alongside `data` /
  `instance` / `parent` it sees whatever the calling service passed as
  `context=` — `user` and `request` when the default model services are
  driving, since they populate it from their own kwargs pool. Declare
  `user` and it arrives; declare nothing but `data` and nothing else is
  passed.

Two create/update slots rather than one because the two shapes genuinely
differ, and a single slot would have to fake `instance=None`. Nested services
run with `atomic=False`: the calling service's atomic block already wraps the
whole tree, and a block per row would only buy a savepoint per row. In the
async helpers the slot must be an `async def` — the async path is awaited end to
end, and a sync callable there fails on an un-awaitable return.

## When a row's write is refused

An error a row's write raises arrives under the relation that carried it, in the
shape DRF's `ListSerializer` uses:

```python
# a collection: a list as long as the one you sent, {} against the rows that passed
{"posts": [{}, {"title": ["Too rude."]}]}

# a relation holding one row: the payload under the name
{"profile": {"bio": ["Too long."]}}
```

Namespacing it is not decoration. A row shares field names with its parent — an
`Author` with a `title` of its own is indistinguishable from its post's — and a
collection otherwise never says *which* row was refused.

- Both error types are covered: a `create_service` / `update_service` may raise
  `ServiceValidationError` or DRF's `ValidationError`, and the one you raise is
  the one the caller gets, status mapping and all.
- The detail is passed through untouched. A service that raises a string or a
  list gets a string or a list back, at the relation (and position) it came
  from; the library never reshapes it into a field map it did not name.
- Names nest as you walk them, so a grandchild reads
  `{"posts": [{"comments": [{"body": ["Too long."]}]}]}`.

The library's own refusals — the [primary-key
guard](#a-nested-create-may-not-carry-a-primary-key), a [`scope`](#scope) that
matched nothing — already name their relation and are unchanged; they report the
message under the relation name rather than against a row, because they are
about the payload rather than about a row's write.

The relation name here is the map key, which is also the key the payload is read
from. Where a serializer aliased the nested field — `writer =
AuthorSerializer(source="author")`, so the helper is handed `{"author": {...}}`
and the relation has to be declared as `"author"` — the dispatcher translates
the whole error back to the names the request used, at every depth, before the
caller sees it. See [Field names come back as the request spelled
them](../errors-and-atomic.md#field-names-come-back-as-the-request-spelled-them).
Nothing is needed on the spec.

## Declarative — no service body

The default model services forward `relations=` / `children=`, so a
parent-with-relations resource needs no hand-written service:

```python
from rest_framework_services import ServiceSpec, create_model

ServiceSpec(
    service=create_model(Author, relations={"books": ChildSpec(model=Book, fk="author")}),
    input_serializer=AuthorInput,  # validates name + the books list
)
```

`relations` lives on the **helper**, never on `ServiceSpec` — the dispatch
surface stays about transport, not persistence shape.

## Deleting

`delete_model` accepts the same map, to cascade explicitly where the database
will not: a `PROTECT` relation, or a `soft_delete` Django never cascades through
because no row is deleted.

```python
delete_model(
    Catalog,
    relations={
        "sections": ChildSpec(model=Section, fk="catalog"),
        "attachments": GenericRelationSpec(model=Attachment),
        "tags": ManyToManySpec(model=Tag),
    },
)
```

**One rule covers every kind: the cascade removes the rows the parent owns and
leaves alone the rows it merely points at.**

- Reverse-FK collections, reverse one-to-ones and generic relations are the
  parent's rows, so they go — deepest first, by the same [orphan
  rule](#what-happens-to-an-orphan) the update path uses (nullable links
  unlinked and the rest deleted, unless `orphan=` says otherwise), or handed to
  `delete_service`. A flag that meant one thing on update and another on delete
  would be worse than no flag.
- A many-to-many loses only its **membership**. The targets are shared, so none
  is deleted; they are reported under `unlinked`.
- A forward relation is left **untouched**. The link lives on the parent, so the
  column goes when the parent does, and the target is not the parent's to
  remove. It is reported rather than refused, because the same `relations=` map
  is what the row's write path is declared with.

The specs' write-only fields (`match_key` / `mode` / `field_map` / `m2m`) are
ignored here; `orphan` is not one of them — it is about disposal, which is all
this does. For a plain hard delete you usually don't need this at all — the FK's
`on_delete` already cascades.

## What changed in the response

`ChangeResult` reports relations in two carriers, split by **shape** rather than
by keyword:

- `ChangeResult.children` — one
  [`ChildCollectionChange`](../reference/types.md#childcollectionchange) per
  relation that holds *many* rows (reverse FK, generic, many-to-many), with the
  `created` / `updated` / `deleted` / `unlinked` / `removed` pks.
- `ChangeResult.relations` — one
  [`RelatedObjectChange`](../reference/types.md#relatedobjectchange) per
  relation that holds *one* (forward, reverse one-to-one), with a single
  `outcome` and `pk`. Four pk tuples cannot report a one-row relation honestly:
  every one of them would be empty or a one-tuple, and "which of the four is
  non-empty" is a worse way to say what happened than saying it.

```python
result = update_from_input(author, data, relations=RELATIONS)
result.get_child_change("books").created
result.get_relation_change("profile").outcome  # "created" | "updated" | "unlinked" | ...
```

A forward relation shows up **twice** and means two different things: in
`relations` as the row that was created or matched, and in `changes` as the
parent's foreign-key column — which only appears if it actually changed.

`removed` is the fifth collection bucket, and only a `delete_service` fills it:
once a service owns the row, "deleted" and "unlinked" are no longer things the
loop knows.

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
        instance, data, relations={"books": ChildSpec(model=Book, fk="author")}
    ).instance
```

Field-level validation stays in the serializer / dataclass; the helper owns the
writes, inside the service's atomic block. The nested serializer no longer needs
a `create()` / `update()` override, and the writable-nested base class can go.

### What is covered

Forward FK, forward one-to-one, reverse FK collections, reverse one-to-one,
many-to-many (both sides), and generic relations — declared with the spec for
the kind, in one `relations=` map.

### What is not covered

**Many-to-many through an explicit `through` model.** The loop writes target
rows and lets Django write the through row, which cannot carry the extra columns
a custom through model exists for. Declare that relation as a `ChildSpec` on the
through model itself (its two foreign keys are ordinary forward relations), or
write it in a service.

### Three differences to meet here rather than in production

**1. Matching by key is scoped, and unscoped specs are create-only.**
`drf-writable-nested` and `drf-nested` match every by-pk lookup against
`Model.objects.get(pk=pk)` — global, on every kind. This library scopes it: to
the parent's own manager where there is one, and to a declared
[`scope=`](#scope) where there isn't. A spec with neither cannot update an
existing row at all, and a payload that asks it to raises. If you are porting a
declaration, the pk-matching relations are the ones that need a `scope=`.

**2. Orphan removal is `mode=`, and unlinks where it can.** `drf-nested` hard
`delete()`s every unmatched row, triggered by the request not being a PATCH.
Here the trigger is an explicit `mode="replace"` (the default) versus
`"merge"`, and an unmatched row is **unlinked** when its link is nullable and
deleted only when it is not — mirroring `SET_NULL` versus `CASCADE`. A
many-to-many target is never deleted at all.

If you are porting a declaration that relied on the unconditional delete,
`orphan="delete"` is the mechanical answer: it reproduces it on the relations
whose link is nullable, and says so in the spec rather than leaving it to the
column. See [What happens to an orphan](#what-happens-to-an-orphan).

**3. A nested create carrying a primary key raises.** This is the one a
migrating reader hits first, because `drf-nested` upserts by pk on every kind
and payloads written for it carry `id` everywhere. Here a pk that the match did
not resolve is refused rather than saved — see [A nested create may not carry a
primary key](#a-nested-create-may-not-carry-a-primary-key) for why
`Model(pk=7).save()` makes that a security question rather than a style one.
Rows the parent already owns still update by pk exactly as before; what changes
is that a pk naming somebody else's row, or no row at all, now gets a 400
instead of writing.

**What does not change is the error shape.** A refused row reports under its
relation name, aligned against the incoming list for a collection — the same
shape a writable-nested serializer produced, so a client (and any error handling
written against it) needs no change. See [When a row's write is
refused](#when-a-rows-write-is-refused).

Also deliberately absent: `drf-nested`'s per-relation `allow_create` /
`allow_update` / `preserve_provided` / `forbidden_on_create` knobs. They exist
because that facility is serializer-declarative and needs override points. Here
you control what reaches a spec, and a row that needs real behaviour gets a
service in the slot.
