# Scaffold a service app

The package ships a `startserviceapp` management command — a subclass
of Django's `startapp` that lays out an app the way this library
encourages.

## Setup

Add the package to `INSTALLED_APPS` to make the command discoverable:

```python
INSTALLED_APPS = [
    ...,
    "rest_framework_services",
]
```

Then:

```bash
python manage.py startserviceapp billing
```

## What you get

```
billing/
├── __init__.py
├── apps.py
├── admin.py
├── urls.py
├── models/__init__.py
├── views/__init__.py
├── services/__init__.py
├── selectors/__init__.py
├── specs/__init__.py
├── validators/__init__.py
├── serializers/__init__.py
├── utils/__init__.py
├── migrations/__init__.py
└── tests/__init__.py
```

Differences vs. plain `startapp`:

- `models/` and `views/` are **packages**, not single files. The single-
  file shape is a poor fit when each model and each view is its own
  unit of work.
- `services/`, `selectors/`, `specs/`, `validators/`, `serializers/`,
  `utils/` are added as packages alongside.
- `specs/` is a conventional home for `ServiceSpec` / `SelectorSpec`
  instances — the wiring that pairs a service or selector with its
  serializers and per-action kwargs provider.
- An empty `urls.py` is included — `startapp` doesn't ship one.

## A note on `validators/`

The `validators/` package is a stylistic convention, not a library
feature. The library doesn't import from it or look it up by name. It
exists to give business-level validation a home of its own — rules like
*"a draft invoice can only be sent if the customer has a verified email"*
or *"refunds beyond 30 days require manager approval"*. These belong
neither in the model nor in the serializer.

The split this layout suggests:

| Concern | Lives in |
|---|---|
| Type validation, required fields, format checks | DRF serializers (`serializers/`), including `DataclassSerializer` for service inputs |
| Business rules / cross-record invariants / external-state checks | Functions in `validators/`, called from services |
| Side effects, persistence, orchestration | `services/` |

## Use it, ignore it, or rename it

The scaffolding is a starting point, not a contract. Nothing in the
library cares whether your service lives in `myapp/services/foo.py` or
`myapp/foo_service.py`. Pick the layout that makes your codebase easier
to navigate; the convention exists because there's value in a project
where every app looks the same.
