# Quickstart

Build a `POST /authors/` endpoint that creates an `Author`. Input is
validated by a dataclass, the service is a plain function, and the
response is shaped by another dataclass — both halves rendered through
`DataclassSerializer` from
[`djangorestframework-dataclasses`](https://pypi.org/project/djangorestframework-dataclasses/),
which the library already depends on.

## 1. Install

```bash
pip install djangorestframework-services
# or, with uv:
uv add djangorestframework-services
```

Add nothing to `INSTALLED_APPS` — there are no models or migrations.
(You only need to add `"rest_framework_services"` if you want to use the
`startserviceapp` management command.)

## 2. Write the input and output dataclasses

Inputs are validated at the view boundary; the service receives a typed
instance.

```python
from dataclasses import dataclass


@dataclass
class CreateAuthorInput:
    name: str
    bio: str = ""


@dataclass
class AuthorOutput:
    id: int
    name: str
    bio: str
```

## 3. Wrap the output in a serializer

`DataclassSerializer` reads attributes via `getattr`, so the service can
return either an `AuthorOutput` instance or a model instance with the
same attribute names.

```python
from rest_framework_dataclasses.serializers import DataclassSerializer


class AuthorOutputSerializer(DataclassSerializer):
    class Meta:
        dataclass = AuthorOutput
```

## 4. Write the service

A plain callable. No DRF imports required; raise framework-agnostic
exceptions if you need to.

```python
from rest_framework_services import create_from_input

from myapp.models import Author


def create_author(*, data: CreateAuthorInput) -> Author:
    result = create_from_input(Author, data)
    return result.instance
```

For services whose entire body is this one-line `create_from_input` /
`update_from_input` / `instance.delete()` glue, the library ships
ready-made factory shortcuts — `create_model(Author)` produces an
equivalent callable inline. See
[Default model services](default-model-services.md) for when to reach
for them vs. writing the service out by hand.

## 5. Wire the view

```python
from rest_framework_services import ServiceCreateView, ServiceSpec


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

## 6. Try it

```bash
curl -X POST http://localhost:8000/authors/ \
  -H 'Content-Type: application/json' \
  -d '{"name": "Ada"}'
```

```json
{ "id": 1, "name": "Ada", "bio": "" }
```

Input validation errors come back as DRF's standard `400` response.
Anything the service raises as `ServiceValidationError` is mapped to
`400`; anything raised as `ServiceError` is mapped to `422`. See
[Errors & atomic](errors-and-atomic.md).

## What changed vs. plain DRF?

- Validation is unchanged — DRF still validates the request before the
  service runs.
- `ModelSerializer.create()` is replaced by your own `create_author`
  function. You write the side effect; the library doesn't hide it.
- The response shape is decoupled from the model. Your service can
  return an `AuthorOutput` directly when the API surface diverges from
  the model.
- The whole thing runs inside `transaction.atomic()` by default.

## Returning the output dataclass directly

When the API surface diverges from the model (computed fields, hidden
columns, denormalised joins), have the service build and return the
output dataclass:

```python
def create_author(*, data: CreateAuthorInput) -> AuthorOutput:
    author = Author.objects.create(name=data.name, bio=data.bio)
    return AuthorOutput(id=author.id, name=author.name, bio=author.bio)
```

`AuthorOutputSerializer` renders it the same way; the view doesn't care.

## Alternative: `ModelSerializer` for output

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

## Where to next

- **[Concepts](concepts.md)** — what services, selectors, and viewsets
  are, and how dispatch works.
- **[Recipes](recipes/index.md)** — composing your own viewset, custom
  actions, passing extra kwargs.
