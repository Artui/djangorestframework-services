# Example: invoices

A minimal Django project showing the full surface of
`djangorestframework-services` on a single resource.

## What it demonstrates

| File | Pattern |
|---|---|
| [`invoices/models/__init__.py`](invoices/models/__init__.py) | Single `Invoice` model |
| [`invoices/services/__init__.py`](invoices/services/__init__.py) | Plain-callable services using `create_from_input` / `update_from_input`; framework-agnostic exceptions; `annotate_invoice` applies exactly the fields a partial call validated; `set_payment_terms` records the days the `terms` action was given; `record_operation` takes `operation` from a `kwargs` provider, not from the caller; `void_drafts` returns the rows it voided, and `void_drafts_summary` returns a count instead |
| [`invoices/selectors/__init__.py`](invoices/selectors/__init__.py) | Plain-callable selectors that act as overrides for `get_queryset()` / `get_object()`; `list_invoices` takes the `status` its spec declares in `reads`, by name |
| [`invoices/views/__init__.py`](invoices/views/__init__.py) | `ServiceViewSet` composing CRUD + four `@service_action`s (`POST /invoices/<id>/send/`, `POST /invoices/<id>/annotate/`, `POST /invoices/<id>/terms/`, `POST /invoices/<id>/record/`); `reads=[QueryParam("status")]` on the list spec; `field_audiences` hiding `updated_at` from an agent on the retrieve spec; `PAYMENT_TERMS_SPEC` declaring its own `json_schema_registry=`, a rule for `NetDaysField` that gives the field a range, a `days` format and the checks the compiled spec runs off HTTP (`POST /invoices/<id>/terms/`). `RECORD_OPERATION_SPEC`, whose `kwargs` provider names `call_context` and reads `action` from it, routed as `POST /invoices/<id>/record/` (the note reads `[operation] record`, the action's name) and run off HTTP by `dispatch_spec` with the name the caller's view carries. Three specs for a caller off HTTP, none routed: `FIELDS_SPEC`, whose `QueryParam("fields")` no callable names, so it is a shape; `VOID_DRAFTS_SPEC`, a service spec with a list output and no selector, which compiles to `returns_many`, and `VOID_DRAFTS_SUMMARY_SPEC`, the same declaration over a service that returns a count |
| [`invoices/serializers/__init__.py`](invoices/serializers/__init__.py) | `ModelSerializer` for the response; `AnnotateInvoiceInput`, whose two required fields a partial call may send one of; `NetDaysField`, a field that declares no bounds because its spec's registry rule states them, with `validate_days` as the route's own check (over HTTP the serializer validates and the rule is not read); `InvoiceFieldsSerializer`, which keeps the fields `?fields=` names, read off `request.query_params`, which off HTTP the kernel's presenter fills with the call's shapes |
| [`invoices/urls.py`](invoices/urls.py) | DRF router registration |
| [`invoices/tests/__init__.py`](invoices/tests/__init__.py) | End-to-end APITestCase covering every endpoint, plus the declared read, the agent-only audience a `dispatch_spec(partial=True)` call off HTTP, and the spec's own registry read three ways: by `spec_to_json_schema` with no `registry=`, by the compiled spec (which keeps the bounds and drops the `days` format the kernel cannot state), and by the kernel's own `dispatch`, which refuses a value outside the range; then six more classes: `CallContextTests` (over HTTP the `record/` route's note reads `[operation] record`, the action's name; off HTTP the caller's `call_context` dict reaches a provider as the same object, filled with the four standard entries), `ShapingQueryParamTests` (`kernel_spec(spec).shapes` holds `fields` and `.reads` does not, and the kernel's `dispatch` plus `present` reach the serializer with it), `ReturnsManyTests` (`returns_many` on the compiled spec, the kernel answering a list, and the edge refusing a mapping with `ImproperlyConfigured`) `CarriedRequestTests` (a permission class sees the principal and the call's query params, with the headers of the request the run arrived on), `KernelRouteRefusalTests` (a precondition raising DRF's `PermissionDenied` reaches a caller of the kernel's `dispatch` as the kernel's `NotPermitted`, still DRF's class, while `dispatch_spec` raises exactly what the author raised) and `UnsendableFieldTests` (a required `user` field is one no call on the kernel's route can send, and the compiled Validator's `unsendable` names it) |

## Run it

```bash
cd examples
python manage.py migrate
python manage.py runserver
```

Then exercise the endpoints:

```bash
# Create
curl -X POST http://localhost:8000/invoices/ \
  -H "Content-Type: application/json" \
  -d '{"customer": "Acme", "amount_cents": 4200, "notes": "Thanks!"}'

# List (with optional ?status= filter)
curl http://localhost:8000/invoices/
curl http://localhost:8000/invoices/?status=draft

# Retrieve
curl http://localhost:8000/invoices/1/

# Update (PATCH)
curl -X PATCH http://localhost:8000/invoices/1/ \
  -H "Content-Type: application/json" \
  -d '{"notes": "Net 30"}'

# Custom action (set customer and notes; both are required over HTTP)
curl -X POST http://localhost:8000/invoices/1/annotate/ \
  -H "Content-Type: application/json" \
  -d '{"customer": "Acme Ltd", "notes": "Net 30"}'

# Custom action (record payment terms, 1 to 90 days)
curl -X POST http://localhost:8000/invoices/1/terms/ \
  -H "Content-Type: application/json" \
  -d '{"days": 30}'

# Custom action (mark as sent)
curl -X POST http://localhost:8000/invoices/1/send/ \
  -H "Content-Type: application/json" \
  -d '{"note": "first reminder"}'

# Custom action (note the operation on the invoice: "[operation] record")
curl -X POST http://localhost:8000/invoices/1/record/

# Delete
curl -X DELETE http://localhost:8000/invoices/1/
```

## Run the test

```bash
python manage.py test invoices
```

From the repository root, `make examples` runs the same suite, as CI does on
every pull request.

The suite walks the full create → list → retrieve → patch → custom-action → delete flow plus error-path assertions, and checks the declared read, the agent-only audience, a partial call made off HTTP with `dispatch_spec(partial=True)`, and what the payment-terms spec's registry does to its schema, its compiled spec and the kernel's `dispatch`. It also runs four specs off HTTP: a `call_context` passed to `dispatch_spec`, a shaping `QueryParam` presented by the kernel, a list output that returns its own rows, and a carried Django request.
