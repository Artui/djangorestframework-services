# djangorestframework-services

A **service / selector layer** for Django REST Framework.

DRF's default mode for mutating endpoints is "the serializer is the business
logic". That's fine for thin CRUD, but it falls apart the moment you need
to compose with an external system, fan out side effects, or write logic
that doesn't belong on a model. `djangorestframework-services` keeps DRF's
routing, validation, and serialization for what they're good at, and gives
you a precise, well-typed seam for the bits in the middle.

## What you get

- **Services** — plain callables. The library does not define a `Service`
  base class or prescribe a signature.
- **Selectors** — plain callables that override `get_queryset()` /
  `get_object()`. Filter backends, pagination, and serialization stay
  vanilla DRF.
- **Mutation helpers** — `create_from_input`, `update_from_input`,
  `apply_input`, plus async siblings `acreate_from_input` /
  `aupdate_from_input`, with change tracking, no surprises.
- **Sync and async** services and selectors, transparently dispatched.
- **Atomic by default**, opt-out per spec.
- **Framework-agnostic exceptions** — services don't import from DRF.
- **Typed end-to-end** — generic `ServiceSpec[InputT, ResultT]` plus
  lenient and strict Protocols that catch signature drift at type-check
  time, with fail-fast validation at `as_view()`. See
  [Typing](typing.md).

## When to use this

Reach for it when an endpoint:

- coordinates more than one model write
- talks to an external system (payments, email, queue)
- has business rules that don't belong on a model or in a serializer
- needs to differ in input shape vs. output shape (computed fields,
  hidden columns, denormalised joins)
- needs to mix sync and async paths

Skip it when an endpoint is "serialize → save → respond". DRF's
`ModelSerializer` already does that well; this library is for the cases
where it doesn't.

## Install

```bash
pip install djangorestframework-services
# or, with uv:
uv add djangorestframework-services
```

That brings in `djangorestframework-dataclasses` automatically — it's
the recommended way to wire dataclass-shaped inputs and outputs. Plain
`Serializer` / `ModelSerializer` work everywhere too.

## Where to next

- **[Quickstart](quickstart.md)** — a `POST /authors/` endpoint, end to end.
- **[Concepts](concepts.md)** — the three building blocks (service,
  selector, view) and how dispatch works.
- **[Mutation helpers](mutation-helpers.md)** — `create_from_input`,
  `update_from_input`, `apply_input`, and the `ChangeResult` they return.
- **[Typing](typing.md)** — lenient and strict Protocols, per-spec
  `kwargs=` providers, fail-fast `as_view()` validation.
- **[Errors & atomic](errors-and-atomic.md)** — framework-agnostic
  exceptions and the transaction model.
- **[Async](async.md)** — when and how to use `async def` services.
- **[Recipes](recipes/index.md)** — task-shaped how-tos.
- **[Reference](reference/index.md)** — autodocumented public API.

## Compatibility

| Axis | Range |
|---|---|
| Python | 3.10 – 3.14 |
| Django | 4.2, 5.0, 5.1, 5.2, 6.0 |
| DRF | ≥ 3.14 |

CI runs the full Python × Django matrix with 100% coverage gating, minus
the cells the upstream version-support docs already rule out (e.g. Django
6.0 only runs on Python 3.12+).
