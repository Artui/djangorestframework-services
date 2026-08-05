# State rules with `preconditions`

Some rules aren't about the shape of a payload — they're about whether the
operation is allowed *right now*, given the row and the actor. A budget that's
been locked. An order already shipped. A reviewer who already has a pending
request on this object.

`preconditions` is a declared slot for those, on the spec, so the rule travels
with the operation to every transport instead of living in one view method.

```python
from rest_framework_services import ServiceError, ServiceSpec


class BudgetLocked(ServiceError):
    status_code = 409


def budget_not_locked(*, instance):
    if instance.is_locked:
        raise BudgetLocked("This budget is locked and cannot be edited.")


spec = ServiceSpec(
    service=update_budget,
    input_serializer=BudgetInput,
    preconditions=[budget_not_locked],
)
```

## Two rules you have to know

**Raise to abort. The return value is ignored.**

This is the one that bites. A predicate written the natural way —

```python
def budget_not_locked(*, instance) -> bool:
    return not instance.is_locked          # ← does nothing at all
```

— is silently a no-op. Returning `False` does not block the call; only raising
does. If you are porting existing `-> bool` predicates, every one of them needs
a raise.

**Raise `ServiceError`, not a DRF exception.**

`ServiceError` (and `ServiceValidationError`) is the framework-agnostic error
every transport already maps: to a response over HTTP, to a failed tool result
over MCP, to a retryable error under an agent toolset. A DRF `APIException`
subclass works over HTTP and escapes the mapping everywhere else — the same rule
lands as a clean 409 for a browser and an unhandled internal error for an agent.

## Where they fire

```
permissions → target resolution → validation → preconditions → service
```

That position is what lets one field cover both kinds of rule. By the time a
precondition runs, all of these are in the pool:

| Key | When |
|---|---|
| `instance` | the resolved row (update / destroy / retrieve) |
| `collection` | the resolved set (collection mutations, LIST selectors) |
| `data`, `serializer` | whenever the spec has an `input_serializer` |
| `user`, `request` | always |

So a rule about the row, a rule about the payload, and a rule that needs both
are all the same kind of thing. Declare the keys you want and you get exactly
those:

```python
def price_increase_needs_approval(*, instance, data, user):
    if data["price"] > instance.price * 2 and not user.is_staff:
        raise NeedsApproval("Doubling a price requires an approver.")
```

Business logic never sees an unvalidated payload — validation has already run.

## Preconditions vs. serializer validation

Keep the boundary. A serializer validates **shape**: types, required fields,
field-level formats, cross-field coherence within the payload itself. A
precondition validates **state**: what's in the database, who's asking, what
already happened.

The practical test is whether the rule can be answered from the payload alone.
`end_date > start_date` can — that's `Serializer.validate()`. "This budget is
locked" can't; it needs a row. Putting state rules in the serializer means
fetching the object twice and reimplementing the lookup the spec already did.

## Selectors

`SelectorSpec` takes the field too, with one position: after the target
resolves. `RETRIEVE` seeds `instance`, `LIST` seeds `collection`.

```python
SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=budget_by_pk,
    preconditions=[budget_is_visible_during_freeze],
)
```

Pool binding does the discrimination — a precondition declaring `instance`
cannot be attached to a `LIST` spec, and you'll hear about it at `as_view()`
rather than at request time.

## Bulk

A `many=True` bulk spec runs its preconditions **once**, with no target, before
the service — matching where the target guard fires. Only preconditions
declaring `user` / `request` / `data` bind. Per-item rules belong in the
service's own loop, where you can report which item failed.

## What's checked at startup

`as_view()` rejects, with a message naming the problem:

- a bare callable instead of a sequence (`preconditions=check` — wrap it in a list)
- a non-callable element, named by index
- a parameter nothing seeds. **The pool is keyed on framework seed names, not on
  your model names** — `def is_editable(order)` will not receive your `Order`;
  write `def is_editable(*, instance)`. Without this check that's a `TypeError`
  deep in dispatch, which surfaces as a 500.
- `preconditions` on a *nested* spec (`instance_selector_spec`,
  `collection_selector_spec`, `output_selector_spec`). Those never dispatch, so
  the field would never run; it's refused rather than ignored.

## Telling an agent about them

Preconditions are deliberately **not** reflected into tool schemas. There's
nothing sound to derive the text from — a function's `__name__` and `__doc__`
are the only machine-readable strings on it, and generating agent-facing schema
from either makes a docstring edit a wire-format change.

If you want an agent to know an operation can fail this way, say so in
`spec.metadata`, in the words you want it to read, and surface that from your
own adapter. The retryable-vs-fatal distinction rides on the exception: a 409
tells an agent the call could succeed later, a 403 tells it not to bother.
