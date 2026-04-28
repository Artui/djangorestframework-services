# Custom action with `@service_action`

DRF's `@action` decorator adds extra routes to a viewset. `@service_action`
wraps `@action` and routes the call through the same
validate-dispatch-render plumbing as the standard mutation actions —
input is validated against the spec's `input_serializer`, the service
is dispatched with `resolve_callable_kwargs`, exceptions are mapped to
DRF responses, output is rendered through `output_serializer`, and the
whole call is wrapped in `transaction.atomic()` (unless the spec opts
out).

## Example: approve an invoice

```python
from dataclasses import dataclass

from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import ServiceSpec, ServiceViewSet, service_action


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


def approve_invoice(*, instance, data):
    instance.status = "approved"
    instance.note = data.note
    instance.save(update_fields=["status", "note"])
    return instance


class InvoiceViewSet(ServiceViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceDetailSerializer

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

Routed as `POST /invoices/{pk}/approve/`. The decorated method body is
**not executed** — the decorator supplies the handler. Keep the body
because:

- the docstring becomes the action description (visible in the browsable
  API and DRF spectacular),
- the function name becomes the URL segment unless you set `url_path`,
- it's a place to attach `@action`-compatible kwargs (`url_path`,
  `url_name`, `permission_classes`, `throttle_classes`, `serializer_class`,
  …).

## Read-only actions

Skip `input_serializer` for actions that don't take a body. The service
just receives `instance` (and any other kwargs it asks for):

```python
def export_invoice(*, instance):
    return render_pdf(instance)


class InvoiceViewSet(ServiceViewSet):
    @service_action(
        ServiceSpec(service=export_invoice),
        detail=True,
        methods=["get"],
    )
    def export(self, request, pk=None):
        """Render the invoice as PDF."""
```

## Collection-level actions

Set `detail=False` and the URL is `<basename>/<action>/`:

```python
@service_action(
    ServiceSpec(service=archive_old_invoices),
    detail=False,
    methods=["post"],
)
def archive_old(self, request):
    """Archive every invoice older than 90 days."""
```

The service receives `request` (and not `instance`). `archive_old_invoices`
can declare any subset of `{request, user, view, data}` it needs.

## Why not just `@action`?

`@action` gives you the URL, but you write the dispatch yourself —
validation, kwarg pool, exception mapping, atomic wrapping. `@service_action`
does that for you. If you're already paying the cost of writing a
service for the standard CRUD actions, custom actions get the same
treatment for free.
