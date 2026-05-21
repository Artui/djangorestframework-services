"""Viewset wiring services and selectors to routes."""

from __future__ import annotations

from invoices.models import Invoice
from invoices.selectors import get_invoice, list_invoices
from invoices.serializers import InvoiceSerializer
from invoices.services import (
    ApproveInput,
    CreateInvoiceInput,
    UpdateInvoiceInput,
    create_invoice,
    delete_invoice,
    mark_invoice_sent,
    update_invoice,
)
from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    service_action,
)


def _invoice_response_spec() -> SelectorSpec[Invoice, None]:
    """The post-mutation re-fetch shared by every write action."""

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        output_serializer=InvoiceSerializer,
    )


class InvoiceViewSet(ServiceViewSet):
    queryset = Invoice.objects.all()
    serializer_classes = {
        "list": InvoiceSerializer,
        "retrieve": InvoiceSerializer,
    }

    service_specs = {
        "list": list_invoices,
        "retrieve": get_invoice,
        "create": ServiceSpec(
            service=create_invoice,
            input_serializer=CreateInvoiceInput,
            output_selector_spec=_invoice_response_spec(),
        ),
        "update": ServiceSpec(
            service=update_invoice,
            input_serializer=UpdateInvoiceInput,
            output_selector_spec=_invoice_response_spec(),
        ),
        "destroy": ServiceSpec(service=delete_invoice),
    }

    @service_action(
        ServiceSpec(
            service=mark_invoice_sent,
            input_serializer=ApproveInput,
            output_selector_spec=_invoice_response_spec(),
        ),
        detail=True,
        methods=["post"],
    )
    def send(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Mark a draft invoice as sent."""
