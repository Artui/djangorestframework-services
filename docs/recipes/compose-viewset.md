# Compose your own viewset

`ServiceViewSet` is the full-CRUD composition of every per-action
mixin. When you want fewer actions, compose the mixins yourself — they
are part of the public API.

## Read-only viewset

`SelectorViewSet` is a pre-built composition of `SelectorListMixin` +
`SelectorRetrieveMixin` + DRF's `GenericViewSet`:

```python
from rest_framework_services import SelectorViewSet


class AuthorReadOnly(SelectorViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorDetailSerializer
    service_specs = {
        "list": list_authors,
        "retrieve": get_author,
    }
```

Or compose it yourself, if you want to mix in something custom:

```python
from rest_framework.viewsets import GenericViewSet
from rest_framework_services import SelectorListMixin, SelectorRetrieveMixin


class AuthorReadOnly(SelectorListMixin, SelectorRetrieveMixin, GenericViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorDetailSerializer
    service_specs = {
        "list": list_authors,
        "retrieve": get_author,
    }
```

## Create + retrieve only

Useful for resources that are written once and then read, never
updated.

```python
from rest_framework.viewsets import GenericViewSet
from rest_framework_services import (
    MultiSerializerMixin,
    SelectorRetrieveMixin,
    ServiceCreateMixin,
    ServiceSpec,
)


class TicketViewSet(
    ServiceCreateMixin,
    SelectorRetrieveMixin,
    MultiSerializerMixin,
    GenericViewSet,
):
    queryset = Ticket.objects.all()
    serializer_classes = {
        "retrieve": TicketDetailSerializer,
    }
    service_specs = {
        "retrieve": get_ticket,
        "create": ServiceSpec(
            service=open_ticket,
            input_serializer=OpenTicketInput,
            output_serializer=TicketDetailSerializer,
        ),
    }
```

## Available mixins

| Mixin | Action | DRF method |
|---|---|---|
| `ServiceCreateMixin` | `create` | `POST` |
| `ServiceUpdateMixin` | `update` / `partial_update` | `PUT` / `PATCH` |
| `ServiceDestroyMixin` | `destroy` | `DELETE` |
| `SelectorListMixin` | `list` | `GET` (collection) |
| `SelectorRetrieveMixin` | `retrieve` | `GET` (detail) |
| `MultiSerializerMixin` | — | per-action `serializer_class` dispatch |
| `MutationFlowMixin` | — | the building block for service-backed action flow on bespoke shapes that don't fit the existing five mixins |

`MultiSerializerMixin` is independent of any action and is safe to mix
into any viewset (or skip if every action uses the same serializer).

## When to reach for `MutationFlowMixin`

Almost never directly — `ServiceCreateMixin` etc. compose it. Reach
for it when you have a non-DRF action shape that needs the same
validate-dispatch-render flow (e.g. a websocket message handler, a
GraphQL mutation backend). It is exported precisely so you can build
those shapes without copying the dispatch code.
