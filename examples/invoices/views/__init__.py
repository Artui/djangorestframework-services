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
from rest_framework_services import ServiceViewSet, service_action


class InvoiceViewSet(ServiceViewSet):
    queryset = Invoice.objects.all()
    serializer_classes = {
        "list": InvoiceSerializer,
        "retrieve": InvoiceSerializer,
    }

    list_selector = list_invoices
    retrieve_selector = get_invoice

    create_service = create_invoice
    create_input_dataclass = CreateInvoiceInput
    create_output_serializer = InvoiceSerializer

    update_service = update_invoice
    update_input_dataclass = UpdateInvoiceInput
    update_output_serializer = InvoiceSerializer

    destroy_service = delete_invoice

    @service_action(
        detail=True,
        methods=["post"],
        service=mark_invoice_sent,
        input_dataclass=ApproveInput,
        output_serializer=InvoiceSerializer,
    )
    def send(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Mark a draft invoice as sent."""
