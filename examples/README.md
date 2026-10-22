# Example: invoices

A minimal Django project showing the full surface of
`djangorestframework-services` on a single resource.

## What it demonstrates

| File | Pattern |
|---|---|
| [`invoices/models/__init__.py`](invoices/models/__init__.py) | Single `Invoice` model |
| [`invoices/services/__init__.py`](invoices/services/__init__.py) | Plain-callable services using `create_from_input` / `update_from_input`; framework-agnostic exceptions; `annotate_invoice` applies exactly the fields a partial call validated; `set_payment_terms` records the days the `terms` action was given |
| [`invoices/selectors/__init__.py`](invoices/selectors/__init__.py) | Plain-callable selectors that act as overrides for `get_queryset()` / `get_object()`; `list_invoices` takes the `status` its spec declares in `reads`, by name |
| [`invoices/views/__init__.py`](invoices/views/__init__.py) | `ServiceViewSet` composing CRUD + two `@service_action`s (`POST /invoices/<id>/send/`, `POST /invoices/<id>/annotate/`); `reads=[QueryParam("status")]` on the list spec; `field_audiences` hiding `updated_at` from an agent on the retrieve spec; `PAYMENT_TERMS_SPEC` declaring its own `json_schema_registry=`, a rule for `NetDaysField` that gives the field a range, a `days` format and the checks the compiled spec runs off HTTP (`POST /invoices/<id>/terms/`) |
| [`invoices/serializers/__init__.py`](invoices/serializers/__init__.py) | `ModelSerializer` for the response; `AnnotateInvoiceInput`, whose two required fields a partial call may send one of; `NetDaysField`, a field that declares no bounds because its spec's registry rule states them, with `validate_days` as the route's own check (over HTTP the serializer validates and the rule is not read) |
| [`invoices/urls.py`](invoices/urls.py) | DRF router registration |
| [`invoices/tests/__init__.py`](invoices/tests/__init__.py) | End-to-end APITestCase covering every endpoint, plus the declared read, the agent-only audience a `dispatch_spec(partial=True)` call off HTTP, and the spec's own registry read three ways: by `spec_to_json_schema` with no `registry=`, by the compiled spec (which keeps the bounds and drops the `days` format the kernel cannot state), and by the kernel's own `dispatch`, which refuses a value outside the range |

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

# Delete
curl -X DELETE http://localhost:8000/invoices/1/
```

## Run the test

```bash
python manage.py test invoices
```

From the repository root, `make examples` runs the same suite, as CI does on
every pull request.

The suite walks the full create → list → retrieve → patch → custom-action → delete flow plus error-path assertions, and checks the declared read, the agent-only audience, a partial call made off HTTP with `dispatch_spec(partial=True)`, and what the payment-terms spec's registry does to its schema, its compiled spec and the kernel's `dispatch`.
