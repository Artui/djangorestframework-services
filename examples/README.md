# Example: invoices

A minimal Django project showing the full surface of
`djangorestframework-services` on a single resource.

## What it demonstrates

| File | Pattern |
|---|---|
| [`invoices/models/__init__.py`](invoices/models/__init__.py) | Single `Invoice` model |
| [`invoices/services/__init__.py`](invoices/services/__init__.py) | Plain-callable services using `create_from_input` / `update_from_input`; framework-agnostic exceptions |
| [`invoices/selectors/__init__.py`](invoices/selectors/__init__.py) | Plain-callable selectors that act as overrides for `get_queryset()` / `get_object()` |
| [`invoices/views/__init__.py`](invoices/views/__init__.py) | `ServiceViewSet` composing CRUD + a custom `@service_action` (`POST /invoices/<id>/send/`) |
| [`invoices/serializers/__init__.py`](invoices/serializers/__init__.py) | `ModelSerializer` for the response |
| [`invoices/urls.py`](invoices/urls.py) | DRF router registration |
| [`invoices/tests/__init__.py`](invoices/tests/__init__.py) | End-to-end APITestCase covering every endpoint |

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

The suite walks the full create → list → retrieve → patch → custom-action → delete flow plus error-path assertions.
