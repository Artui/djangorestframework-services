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

## It is a check, not a lock

The answer is correct at the moment of the call. Between the check and the
service committing, another request can ship the order. If the service's
correctness depends on the state, the service still enforces it — inside its
transaction, with a `select_for_update` or a conditional update — and an
affordance is what lets every client find out *before* trying.

## Declaring nothing costs nothing

A spec without `affordances` runs no query and, on the async path, takes no
executor hop for them.

Full signatures: [`Affordance`](../reference/types.md#affordance),
[`ActionUnavailable`](../reference/exceptions.md).
