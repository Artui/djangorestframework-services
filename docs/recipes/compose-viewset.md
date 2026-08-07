# Compose your own viewset

`ServiceViewSet` is the full-CRUD composition of every per-action
mixin. When you want fewer actions, compose the mixins yourself — they
are part of the public API.

## Read-only viewset

`SelectorViewSet` is a pre-built composition of `SelectorListMixin` +
`SelectorRetrieveMixin` + `ActionSerializerResolver` + DRF's `GenericViewSet`:

```python
from rest_framework_services import SelectorKind, SelectorSpec, SelectorViewSet


class AuthorReadOnly(SelectorViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorDetailSerializer,
        ),
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_author,
            output_serializer=AuthorDetailSerializer,
        ),
    }
```

Or compose it yourself, if you want to mix in something custom:

```python
from rest_framework.viewsets import GenericViewSet
from rest_framework_services import (
    ActionSerializerResolver,
    SelectorKind,
    SelectorListMixin,
    SelectorRetrieveMixin,
    SelectorSpec,
)


class AuthorReadOnly(
    SelectorListMixin, SelectorRetrieveMixin, ActionSerializerResolver, GenericViewSet
):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorDetailSerializer,
        ),
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_author,
            output_serializer=AuthorDetailSerializer,
        ),
    }
```

## Create + retrieve only

Useful for resources that are written once and then read, never
updated.

```python
from rest_framework.viewsets import GenericViewSet
from rest_framework_services import (
    ActionSerializerResolver,
    SelectorKind,
    SelectorRetrieveMixin,
    SelectorSpec,
    ServiceCreateMixin,
    ServiceSpec,
)


class TicketViewSet(
    ServiceCreateMixin,
    SelectorRetrieveMixin,
    ActionSerializerResolver,
    GenericViewSet,
):
    queryset = Ticket.objects.all()
    action_specs = {
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_ticket,
            output_serializer=TicketDetailSerializer,
        ),
        "create": ServiceSpec(
            service=open_ticket,
            input_serializer=OpenTicketInput,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=TicketDetailSerializer,
            ),
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
| `ActionSerializerResolver` | — | per-action `output_serializer` dispatch |
| `MutationFlowMixin` | — | the building block for service-backed action flow on bespoke shapes that don't fit the existing five mixins |

`ActionSerializerResolver` is independent of any action and is safe to mix
into any viewset (or skip if every action uses the same serializer via
DRF's `serializer_class`).

## When to reach for `MutationFlowMixin`

Almost never directly — `ServiceCreateMixin` etc. compose it. Reach
for it when you have a non-DRF action shape that needs the same
validate-dispatch-render flow (e.g. a websocket message handler, a
GraphQL mutation backend). It is exported precisely so you can build
those shapes without copying the dispatch code.
