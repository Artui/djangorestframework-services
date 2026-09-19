# What can be done to this object right now

Some operations exist and are permitted and still cannot happen: an order that
shipped cannot be cancelled, a draft with no title cannot be published, refunds
are closed while the books are. That is not a fact about the caller — a
permission class answers that, with a 403 — but a fact about the world, and it
refuses with a 409.

`affordances` declares those facts once, on the operation, so the same
declaration decides whether the call is refused and what the refusal says.

```python
from django.db.models import Exists, OuterRef, Q
from rest_framework_services import Affordance, ServiceSpec

cancel_order = ServiceSpec(
    service=cancel,
    permission_classes=[IsOrderOwner],
    affordances=[
        Affordance(
            code="order_shipped",
            reason="A shipped order cannot be cancelled.",
            when=~Q(status="shipped"),
        ),
        Affordance(
            code="refund_pending",
            reason="Wait for the pending refund to settle.",
            when=~Exists(Refund.objects.filter(order=OuterRef("pk"), settled=False)),
        ),
    ],
)
```

`when` is true when the action **is** available. A caller whose call fails a
condition gets
[`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable]
— over HTTP a `409` whose body names the rule:

```json
{"detail": "A shipped order cannot be cancelled.", "code": "order_shipped"}
```

## A code and a reason are two different things

`code` is stable and machine-readable: it names the rule, never the state that
tripped it. It is what a transport or a client branches on, and it does not
change when somebody rewords the sentence.

`reason` is the sentence people and models both read: it is the message of the
refusal a caller gets, and the sentence a model relays when it explains why an
action is not possible. Write it for them — say what cannot be done and, where
it helps, what would make it possible — and keep internal state out of it:
which reviewer, which batch job, which ledger row is nobody's business but the
code's. A sentence is for reading, not parsing, so branch on the code.

Codes must be unique within a spec, because the code is the only way a reader
tells one refusal from another.

## Two kinds of condition, and the type decides

**An ORM boolean expression is a condition on the row.** A `Q`, an `Exists`, a
lookup: it means exactly what `Order.objects.filter(when)` means, relations
included, and it is answered in SQL. However many are declared, the row they
apply to is checked with **one** query. A `NULL` is not true, so a condition over
a missing relation reads as unavailable.

**A callable is a condition on nothing in particular** — "refunds are closed
during month-end". It is resolved through the keyword pool like any other spec
callable, returns a `bool`, and sees the pool's seeds and nothing else: `user`,
`request`, `progress`, and any
[registered pool seed](../reference/types.md#poolseeds) such as a clock. A
client argument never reaches it, even under a binding that spreads arguments
into the pool — the caller does not get to decide whether the call is allowed.

```python
def books_open(*, clock) -> bool:
    return clock().day < 28


Affordance(code="books_closed", reason="Refunds reopen on the 1st.", when=books_open)
```

A callable result is read for truth, so one that forgets to return refuses
rather than allows.

**A callable over the row is refused at construction**, and that is the point of
the design rather than a limitation of it. A Python predicate over the row has to
run once per row, and a list that reports availability for fifty orders becomes
fifty-one queries. Prefetching does not rescue it: a prefetched relation answers
some reads from its cache and silently re-queries for others. A rule the ORM
cannot express is not an affordance — keep it in
[`preconditions`](preconditions.md), where it is enforced and simply not
advertised.

For the same reason a callable may not name `instance`, `collection`, `data` or
`serializer`. Those exist only once a call is being made, and an affordance is
meant to be answerable without making one.

## Where it runs

```
permissions → target resolution → validation → affordances → preconditions → service
```

**After access, always.** A refusal describes the row's state, and telling a
caller who may not see the row what state it is in is a disclosure. A caller
denied the row gets the 403 and nothing of the affordance.

**Before `preconditions`.** An affordance is the declared, advertisable form of
the same kind of rule, so the one a client could have been told about is the one
that answers.

**In declaration order**, stopping at the first unmet condition.

A condition on the row needs one resolved row. It is refused on a spec with
`many=True` or a `collection_selector_spec` at construction, and at `as_view()`
on an action that targets no row (a create, a non-detail action). A callable condition works on every shape, bulk
included.

## Asking for a whole list

A client deciding which buttons to render should not have to attempt each
action on each row. `SelectorSpec.affordances` asks the question inside the
list query itself:

```python
orders = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=orders_for_user,
    affordances={"cancel": cancel_order, "refund": refund_order},
)
```

The mapping holds the **mutation specs themselves**, keyed by a name you
choose — not registry names, because a `ServiceSpec` does not know what it is
called and the read path should not need a registry to find out. Every
condition on those specs becomes one boolean annotation on each row, named
`affordance__<name>__<code>` (`affordance__cancel__order_shipped`), and all of
them join the **same single `.annotate()` call** as the spec's own
`annotations`. Fifty orders with five conditions is still one query.

Each annotation is the same correlated `Exists` the mutation evaluates when it
is called, so the list and the call cannot disagree about a row. It also means
the answer is read from the table:

- a condition over a multi-valued relation neither duplicates the list's rows
  nor inflates a `Count` you annotated beside it;
- a filtered `Prefetch` on the same relation — which would hide rows from a
  Python predicate reading `order.lines.all()` — does not change the answer.

A callable condition has no row to vary with, so it is answered once per list
and carried as a constant on every row.

### A selector that returns rows instead of a queryset

A selector does not have to return a `QuerySet` for its rows to be answered. A
list, a generator, or a `RETRIEVE` selector's bare instance gets the same answers
under the same `affordance__<name>__<code>` names, so everything that renders them
reads them the same way:

- **Model instances** are answered by **one query per model class present** —
  `Model._base_manager.filter(pk__in=...)` annotated with the very same `Exists`
  expressions — and carry the answers as attributes. Fifty instances with five
  conditions is one extra query, not fifty. A row the table no longer holds
  (deleted between the selector returning it and the check) carries `None` for
  each condition on the row rather than `False`: a call against it would be
  refused as not found, but it fails none of the conditions, so it must not be
  reported as failing one. An instance with no primary key is refused when a
  condition on the row needs finding it.
- **Mappings** come back as **new** mappings with the callable answers added; the
  selector's own objects are left alone. A condition on the row is refused on a
  mapping, which has no model and no primary key to evaluate it against — return
  model instances or a `QuerySet` instead.
- **Anything else** is refused rather than having attributes written onto it.

A generator is materialised once, and that list is what the dispatch returns.

A generated name that collides with a key of `annotations`, or with another
entry's, is refused at construction. `affordances` needs a `selector`, and is
refused on an `instance_selector_spec` or `collection_selector_spec`, whose rows
are acted on rather than returned. On an `output_selector_spec` it answers the
row a mutation hands back.

## What a client reads

Every object rendered from a selector that declares `affordances` carries them
under an `affordances` key — through `render_spec_output`, through the list,
retrieve and `@selector_action` views, and in a mutation's response when its
`output_selector_spec` declares them:

```json
{
  "id": 42,
  "status": "shipped",
  "affordances": {
    "cancel": {
      "available": false,
      "code": "order_shipped",
      "reason": "A shipped order cannot be cancelled."
    },
    "refund": {"available": true}
  }
}
```

`available` is always there. An unavailable answer names the **first** unmet
condition in declaration order — the same one a call would be refused with. The
one exception is a row a selector returned directly that was deleted before its
answers were asked: it reads `{"available": false}` with no `code` and no
`reason`, because it fails none of the conditions and no sentence about it would
be true. (A callable condition declared before the row conditions, and unmet,
still reports its own code and reason; that sentence is true either way.)
Reading the answers costs nothing: they are the annotations the list query
already computed, or the answers attached to rows a selector returned directly.

**An agent reads the same answers a browser does.**
[`render_for_audience`][rest_framework_services.dispatch.render_for_audience.render_for_audience]
passes them through whole. A model speaks human language, so the `reason` is
what it relays when it explains why an action is not possible, and the `code` is
still there for the transport — or the model — to branch on. That is why the
reason is written for people and models alike and keeps internal state out.

The output schema declares the object too:
[`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
adds it, with `code` enumerated from the declaration so a client can switch on it
exhaustively, and
[`output_to_json_schema`][rest_framework_services.jsonschema.output_to_json_schema.output_to_json_schema]
takes the mapping as `affordances=` for a transport that builds its schema from
the serializer. With or without a `projection` the object it declares is the
same, `reason` included, because the payload is.

Rendering refuses, rather than quietly dropping the answers, when the spec has no
`output_serializer`, when the serializer already renders a field called
`affordances`, or when the row being rendered did not come through the selector
that computes them.

## Offering an operation at all

A callable condition does not vary with the row, so it also answers a question
about the operation itself: is it worth offering right now? A transport that lists
operations, such as an MCP server's tool list or an agent's toolset for its next
step, can leave out one that no argument could make succeed:

```python
from rest_framework_services import base_pool, unmet_operation_affordance

pool = base_pool(user=user, request=request, seeds=seeds)
offered = {
    name: spec
    for name, spec in operations.items()
    if unmet_operation_affordance(spec, pool, reserved=seeds.reserved) is None
}
```

[`unmet_operation_affordance`][rest_framework_services.dispatch.unmet_operation_affordance.unmet_operation_affordance]
returns the first unmet callable condition in declaration order, or `None`. A
condition on the row is skipped without a query, because there is no row yet,
and is still answered for each object: on a selector's rows and at the call. A
`SelectorSpec` always answers `None`, because its `affordances` describes other
operations, not the selector.

Build the pool the way the call's would be built, with the same `seeds`, and pass
`reserved=seeds.reserved`. The list and the call then answer every condition from
the same names, through one definition.

**The answer is advisory.** The call still enforces every affordance. If a
condition flips just after the list is built, calling the stale entry gets the
same `ActionUnavailable` a direct call would, with the code the list would now
report. A condition driven by a clock has no event: it is answered when someone
asks, and nothing announces the moment it flips. A transport that pushes list
changes to its client needs another signal for when to ask again.

## It is a check, not a lock

The answer is correct at the moment of the call. Between the check and the
service committing, another request can ship the order. If the service's
correctness depends on the state, the service still enforces it — inside its
transaction, with a `select_for_update` or a conditional update — and an
affordance is what lets every client find out *before* trying.

## Declaring nothing costs nothing

A spec without `affordances` runs no query and, on the async path, takes no
executor hop for them. Neither does asking `aunmet_operation_affordance` about a
spec with no callable condition. A selector without `affordances` issues exactly the
query it issued before, its rows carry no extra attribute, and its views, its
rendered payloads and its schemas are what they were.

Full signatures: [`Affordance`](../reference/types.md#affordance),
[`ActionUnavailable`](../reference/exceptions.md).
