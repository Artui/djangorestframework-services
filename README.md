# djangorestframework-services

[![CI](https://github.com/Artui/djangorestframework-services/workflows/tests/badge.svg)](https://github.com/Artui/djangorestframework-services/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/djangorestframework-services.svg)](https://pypi.org/project/djangorestframework-services/)
[![Python versions](https://img.shields.io/pypi/pyversions/djangorestframework-services.svg)](https://pypi.org/project/djangorestframework-services/)
[![Django versions](https://img.shields.io/pypi/djversions/djangorestframework-services.svg)](https://pypi.org/project/djangorestframework-services/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/djangorestframework-services/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/Artui/djangorestframework-services/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/djangorestframework-services.svg)](LICENSE)

A service / selector layer for Django REST Framework.

DRF's default mode for mutating endpoints is "the serializer is the business
logic". That's fine for thin CRUD, but it falls apart the moment you need
to compose with an external system, fan out side effects, or write logic
that doesn't belong on a model. `djangorestframework-services` keeps DRF's
routing, validation, and serialization for what they're good at, and gives
you a precise, well-typed seam for the bits in the middle.

- **Services** — plain callables. The library does not define a `Service`
  base class or prescribe a signature.
- **Selectors** — plain callables that override `get_queryset()` /
  `get_object()`. Filter backends, pagination, and serialization stay
  vanilla DRF.
- **Mutation helpers** — `create_from_input`, `update_from_input`,
  `apply_input` (and async siblings) with change tracking, no surprises.
- **Sync and async** services and selectors, transparently dispatched.
- **Atomic by default**, opt-out per spec.
- **Framework-agnostic exceptions** — services don't import from DRF.
- **Typed end-to-end** — generic `ServiceSpec[InputT, ResultT]` plus
  lenient and strict Protocols that catch signature drift at type-check
  time, with fail-fast validation at `as_view()`.
- **100% test coverage**, type-checked, Python 3.10–3.14, Django 4.2–6.0.

📖 **Full documentation:** <https://artui.github.io/djangorestframework-services/>

```bash
pip install djangorestframework-services
# or, with uv:
uv add djangorestframework-services
```

---

## Quick start

A `POST /authors/` endpoint that creates an `Author`. Input is validated
by a dataclass, the service is a plain function, and the response is
shaped by another dataclass — both halves rendered through
`DataclassSerializer` from
[`djangorestframework-dataclasses`](https://pypi.org/project/djangorestframework-dataclasses/),
which the library already depends on.

```python
from dataclasses import dataclass

from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import ServiceCreateView, ServiceSpec, create_from_input

from myapp.models import Author


# 1. Input — validated at the view boundary; the service receives a
#    typed instance.
@dataclass
class CreateAuthorInput:
    name: str
    bio: str = ""


# 2. Output — a dataclass that shapes the JSON response. The service
#    can return either the matching dataclass or a model instance with
#    the same attribute names; DataclassSerializer reads via getattr.
@dataclass
class AuthorOutput:
    id: int
    name: str
    bio: str


class AuthorOutputSerializer(DataclassSerializer):
    class Meta:
        dataclass = AuthorOutput


# 3. Service — a plain callable. No DRF imports; raises framework-
#    agnostic exceptions if it needs to.
def create_author(*, data: CreateAuthorInput) -> Author:
    result = create_from_input(Author, data)
    return result.instance


# 4. View — wires it all together.
class CreateAuthorView(ServiceCreateView):
    spec = ServiceSpec(
        service=create_author,
        input_serializer=CreateAuthorInput,
        output_serializer=AuthorOutputSerializer,
    )
```

```python
# urls.py
from django.urls import path
from myapp.views import CreateAuthorView

urlpatterns = [path("authors/", CreateAuthorView.as_view())]
```

POST `{"name": "Ada"}` → `201` with `{"id": 1, "name": "Ada", "bio": ""}`.

### Returning the output dataclass directly

If you want the service's return type to *be* the response shape — useful
when the API surface diverges from your model (computed fields, hidden
columns, denormalised joins) — have the service build and return the
output dataclass:

```python
def create_author(*, data: CreateAuthorInput) -> AuthorOutput:
    author = Author.objects.create(name=data.name, bio=data.bio)
    return AuthorOutput(id=author.id, name=author.name, bio=author.bio)
```

`AuthorOutputSerializer` renders it the same way; the view doesn't care.

### Alternative: `ModelSerializer`

When the response mirrors the model exactly, DRF's `ModelSerializer` is
the shorter path and gets the full DRF feature set (relations, nested
serializers, etc.):

```python
from rest_framework import serializers


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ("id", "name", "bio")


class CreateAuthorView(ServiceCreateView):
    spec = ServiceSpec(
        service=create_author,
        input_serializer=CreateAuthorInput,
        output_serializer=AuthorSerializer,
    )
```

Both patterns are first-class — the library doesn't care which kind of
DRF serializer you use for output. Pick `DataclassSerializer` when you
want the API contract to live alongside the service signature; pick
`ModelSerializer` when the response mirrors the model and you want
DRF's relational machinery.

---

## Mental model

There are three building blocks:

| Block | What it is | Where it lives |
|---|---|---|
| **Service** | A plain callable that performs a mutation | Your code |
| **Selector** | A plain callable that returns data to read | Your code |
| **View / Viewset** | DRF view that wires a service or selector to an HTTP method | Provided by this library |

Views inspect the service / selector signature with `inspect.signature` and
pass only the arguments they declare from a known pool: `request`, `user`,
optionally `instance` (update / delete) and `data` (when an
`input_serializer` is configured), plus extras from `get_service_kwargs()` /
`get_selector_kwargs()`. If a callable declares `**kwargs`, the entire pool
is forwarded. `view` is intentionally not in the pool — pipe view state
through `ServiceSpec.kwargs` / `SelectorSpec.kwargs` (which receive a narrow
`ServiceView`) or `get_<action>_*_kwargs` instead.

```python
def create_author(*, data, user):       # the view passes only data + user
    return Author.objects.create(name=data.name, created_by=user)

def list_authors(*, request):           # request is in the pool
    return Author.objects.filter(...)
```

---

## Mutation helpers

The library doesn't run your services for you, but it does ship the helpers
that DRF's `serializer.save()` quietly performs — minus the surprises and
plus a typed change record.

```python
from rest_framework_services import update_from_input, UNSET

def update_author(*, instance, data):
    result = update_from_input(instance, data, exclude_fields=["created_by"])
    if result.get_field_change("email"):
        send_email_changed_notice(instance)
    return result.instance
```

What you get:

- **`apply_input(instance, data)`** — set attributes in memory, no save.
- **`create_from_input(Model, data)`** — build, save, optional M2M.
- **`update_from_input(instance, data)`** — diff in-memory state vs. input,
  call `save(update_fields=[...])` with only the fields that actually
  changed.
- **`acreate_from_input` / `aupdate_from_input`** — async equivalents
  using Django 4.2+ `asave()` / `aset()`.

All of them accept:

- `data` — a dataclass, plain dict, or any object with `__dict__`.
- `field_map: dict[str, str]` — translate input keys to model attribute
  names.
- `exclude_fields: list[str]` — fields to drop from the input before
  applying.
- `m2m: dict[str, Any]` — many-to-many assignments applied post-save
  (create/update only).

All of them return a `ChangeResult`:

```python
@dataclass(frozen=True)
class ChangeResult:
    instance: Model
    created: bool
    changes: tuple[FieldChange, ...]

    @property
    def changed_fields(self) -> tuple[str, ...]: ...
    def get_field_change(self, field_name: str) -> FieldChange | None: ...
    def __bool__(self) -> bool: ...   # True iff any change
```

The `UNSET` sentinel distinguishes "field omitted from input" from "field
explicitly set to `None`" — critical for correct `PATCH` semantics.

---

## Typed services and selectors

Services and selectors are plain callables, but you can pin their shape
to a Protocol so a type checker catches signature drift before request
time.

`CreateService`, `UpdateService`, `DeleteService`, `ListSelector`,
`RetrieveSelector`, and `OutputSelector` are **lenient** Protocols —
they accept `**kwargs: Any`, so your callable can declare only the
parameters it actually uses:

```python
from rest_framework_services import CreateService, ListSelector


def create_author(*, data: CreateAuthorInput, user) -> Author: ...

def list_authors(*, request) -> QuerySet[Author]: ...


_: CreateService[CreateAuthorInput, Author] = create_author
_: ListSelector[Author] = list_authors
```

When you want the type checker to fail on *any* drift — including
extras forwarded from a `kwargs=` provider — use the **strict**
variants. They use [PEP 692](https://peps.python.org/pep-0692/)
`Unpack[TypedDict]` to pin every kwarg the callable receives, and pair
naturally with the `@implements(...)` decorator so the contract sits on
the function definition itself:

```python
from typing import TypedDict
from typing_extensions import Unpack

from django.http import HttpRequest

from rest_framework_services import (
    ServiceSpec,
    ServiceViewSet,
    StrictCreateService,
    StrictListSelector,
    implements,
)


class AuthorExtras(TypedDict):
    tenant_id: int


def _author_kwargs(view, request) -> AuthorExtras:
    return {"tenant_id": request.tenant.id}


@implements(StrictCreateService[CreateAuthorInput, AuthorExtras, Author])
def create_author(
    *,
    data: CreateAuthorInput,
    request: HttpRequest,
    **extras: Unpack[AuthorExtras],
) -> Author: ...


@implements(StrictListSelector[AuthorExtras, Author])
def list_authors(
    *,
    request: HttpRequest,
    **extras: Unpack[AuthorExtras],
) -> QuerySet[Author]: ...


class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorOutputSerializer,
            kwargs=_author_kwargs,
        ),
    }
```

Adding a parameter to the service without updating `AuthorExtras` is a
type error. Removing a key from `AuthorExtras` without updating the
service is a type error.

On top of static typing, `as_view()` walks every spec at URL-wiring time
and raises `ImproperlyConfigured` for misconfigurations the checker
can't see — a service requiring `data` with no `input_serializer`, an
`instance` parameter on a create flow, or a required parameter no
extras provider supplies.

See [Typing services and selectors](https://artui.github.io/djangorestframework-services/typing/)
for the full Protocol catalogue and per-spec `kwargs=` resolution
rules.

---

## Views

| Class | Method | Purpose |
|---|---|---|
| `ServiceCreateView` | `POST` | runs `service` to create |
| `ServiceUpdateView` | `PUT` / `PATCH` | runs `service` to update; instance from `get_object()` |
| `ServiceDeleteView` | `DELETE` | runs `service` to delete |
| `SelectorListView` | `GET` | uses `spec.selector` (or `queryset`) for list |
| `SelectorRetrieveView` | `GET` | uses `spec.selector` (or `queryset` + `lookup_field`) for retrieve |

Mutation views are configured by setting a single `spec` class attribute
to a `ServiceSpec`, which bundles `service`, `input_serializer`,
`output_serializer`, `output_selector`, `atomic`, and `success_status`.
Selector views are configured by setting `spec` to a `SelectorSpec`, which
bundles `selector` and `output_serializer`.

```python
@dataclass
class UpdateAuthorInput:
    name: str | None = None
    bio: str | None = None


class UpdateAuthorView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=update_author,
        input_serializer=UpdateAuthorInput,
        output_serializer=AuthorOutputSerializer,   # DataclassSerializer
    )
```

A `ModelSerializer` can be dropped in just as cleanly:

```python
class UpdateAuthorView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=update_author,
        input_serializer=UpdateAuthorInput,
        output_serializer=AuthorSerializer,         # DRF ModelSerializer
    )
```

When a service returns `None` and the view has an instance in scope (update
or delete), the in-memory instance is rendered — matching DRF's
`UpdateAPIView` shape without you having to wire it up.

---

## Viewsets

`ServiceViewSet` is a router-compatible viewset composed of per-action
mixins. Each action picks its own serializer; below the read side uses
two `DataclassSerializer` shapes (terse list, full detail) and the
mutations reuse the detail one:

```python
from dataclasses import dataclass

from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import SelectorSpec, ServiceSpec, ServiceViewSet


@dataclass
class AuthorListItem:
    id: int
    name: str


@dataclass
class AuthorDetail:
    id: int
    name: str
    bio: str


class AuthorListItemSerializer(DataclassSerializer):
    class Meta:
        dataclass = AuthorListItem


class AuthorDetailSerializer(DataclassSerializer):
    class Meta:
        dataclass = AuthorDetail


class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            selector=list_authors,
            output_serializer=AuthorListItemSerializer,
        ),
        "retrieve": SelectorSpec(
            selector=get_author,
            output_serializer=AuthorDetailSerializer,
        ),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorDetailSerializer,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=UpdateAuthorInput,
            output_serializer=AuthorDetailSerializer,
        ),
        "destroy": ServiceSpec(service=delete_author),
    }
```

`action_specs` is a single action-keyed mapping wiring every action.
Read-side actions (`"list"`, `"retrieve"`) take a `SelectorSpec`;
write-side actions take a `ServiceSpec`. Per-action serializers live on
the spec's `output_serializer` field — `ActionSerializerResolver` (already
mixed into `ServiceViewSet` and `SelectorViewSet`) reads them when DRF
calls `get_serializer_class()`. An absent entry on a write action returns
`405 Method Not Allowed`; a wrong-type entry (e.g. `SelectorSpec` on
`create`) raises `ImproperlyConfigured` at request time.

Mix `ModelSerializer` and `DataclassSerializer` per action freely — the
viewset doesn't distinguish between them.

Register it with a router as usual:

```python
router = DefaultRouter()
router.register("authors", AuthorViewSet, basename="author")
```

Per-action mixins (`ServiceCreateMixin`, `ServiceUpdateMixin`,
`ServiceDestroyMixin`, `SelectorListMixin`, `SelectorRetrieveMixin`) and
`ActionSerializerResolver` are exported so you can compose only the actions
you need:

```python
from rest_framework_services import (
    ActionSerializerResolver,
    SelectorListMixin,
    SelectorRetrieveMixin,
    SelectorSpec,
)


class AuthorReadOnly(
    SelectorListMixin,
    SelectorRetrieveMixin,
    ActionSerializerResolver,
    GenericViewSet,
):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(output_serializer=AuthorListItemSerializer),
        "retrieve": SelectorSpec(output_serializer=AuthorDetailSerializer),
    }
```

`SelectorViewSet` is a pre-built read-only composition.

### `ActionSerializerResolver`

Per-action serializer dispatch driven by `action_specs`:

```python
action_specs = {
    "list": SelectorSpec(output_serializer=ListSerializer),
    "retrieve": SelectorSpec(output_serializer=DetailSerializer),
    "my_custom_action": ServiceSpec(
        service=my_action,
        output_serializer=CustomSerializer,
    ),
}
```

`get_serializer_class()` reads `output_serializer` from the active
action's spec and falls back to DRF's standard `serializer_class` when the
action has no spec or the spec has no `output_serializer`.

### `@service_action`

Custom viewset actions wrapped in the same plumbing as the standard
mutation flow:

```python
from dataclasses import dataclass

from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import ServiceSpec, service_action


@dataclass
class ApproveInput:
    note: str = ""


@dataclass
class InvoiceDetail:
    id: int
    customer: str
    amount_cents: int
    status: str


class InvoiceDetailSerializer(DataclassSerializer):
    class Meta:
        dataclass = InvoiceDetail


class InvoiceViewSet(ServiceViewSet):
    @service_action(
        ServiceSpec(
            service=approve_invoice,
            input_serializer=ApproveInput,
            output_serializer=InvoiceDetailSerializer,
        ),
        detail=True,
        methods=["post"],
    )
    def approve(self, request, pk=None):
        """Approve an invoice."""
```

The decorated method body is *not* executed — the decorator supplies the
handler. The body is there so the action has a docstring, a name (used by
the router), and a place for `@action`-compatible metadata.

---

## Errors

Services raise framework-agnostic exceptions. The view boundary translates
them to DRF responses.

```python
from rest_framework_services import ServiceError, ServiceValidationError

def withdraw(*, instance, data):
    if data.amount > instance.balance:
        raise ServiceValidationError({"amount": ["insufficient funds"]})
    if instance.locked:
        raise ServiceError("account is locked")
    instance.balance -= data.amount
    instance.save(update_fields=["balance"])
    return instance
```

| Raised | Becomes | HTTP |
|---|---|---|
| `ServiceValidationError` | `rest_framework.exceptions.ValidationError` | 400 |
| `ServiceError` | `rest_framework.exceptions.APIException` | 422 |

---

## Atomic transactions

Every service call is wrapped in `transaction.atomic()` by default. Opt out
per spec — `atomic` lives on `ServiceSpec`:

```python
class ImportView(ServiceCreateView):
    spec = ServiceSpec(
        service=run_import,
        input_serializer=ImportInput,
        atomic=False,   # the import service handles its own savepoints
    )
```

---

## Async services

Services and selectors can be `async def`. The dispatcher detects this via
`inspect.iscoroutinefunction` and runs them via `asgiref.sync.async_to_sync`
under sync views, or directly under async views. Atomic wrapping works for
both.

```python
async def fetch_remote(*, request):
    async with httpx.AsyncClient() as client:
        return await client.get("https://...").json()

class FetchView(ServiceCreateView):
    spec = ServiceSpec(service=fetch_remote)
```

---

## `startserviceapp`

A management command that scaffolds a service-oriented Django app:

```bash
python manage.py startserviceapp billing
```

Produces:

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
├── validators/__init__.py
├── serializers/__init__.py
├── utils/__init__.py
├── migrations/__init__.py
└── tests/__init__.py
```

Add `"rest_framework_services"` to `INSTALLED_APPS` to make the command
discoverable.

### A note on `validators/`

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

Use it, ignore it, or rename it — the scaffolding is a starting point,
not a contract.

---

## Examples

A minimal but runnable example project lives in
[`examples/`](examples/). It demonstrates the create / list / retrieve /
update / destroy flows on a single resource and one custom action via
`@service_action`.

```bash
cd examples
python manage.py migrate
python manage.py runserver
```

---

## Compatibility

| Axis | Range |
|---|---|
| Python | 3.10 – 3.14 |
| Django | 4.2, 5.0, 5.1, 5.2, 6.0 |
| DRF | ≥ 3.14 |

CI runs the full Python × Django matrix with 100% coverage gating.

---

## Status

Pre-1.0. Public API is stable but may shift. See
[`CHANGELOG.md`](CHANGELOG.md) for the full release history.

---

## License

MIT.
