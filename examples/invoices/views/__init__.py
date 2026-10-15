"""Viewset wiring services and selectors to routes."""

from __future__ import annotations

from invoices.models import Invoice
from invoices.selectors import get_invoice, list_invoices
from invoices.serializers import AnnotateInvoiceInput, InvoiceSerializer
from invoices.services import (
    ApproveInput,
    CreateInvoiceInput,
    UpdateInvoiceInput,
    annotate_invoice,
    create_invoice,
    delete_invoice,
    mark_invoice_sent,
    update_invoice,
)
from rest_framework_services import (
    FieldMarking,
    QueryParam,
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


# Named so a caller off HTTP dispatches the spec the route serves.
ANNOTATE_SPEC = ServiceSpec(
    service=annotate_invoice,
    input_serializer=AnnotateInvoiceInput,
    output_selector_spec=_invoice_response_spec(),
)


class InvoiceViewSet(ServiceViewSet):
    queryset = Invoice.objects.all()

    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=InvoiceSerializer,
            # The query value the selector reads, declared: the schema lists
            # it, and an off-HTTP caller sends it as an argument.
            reads=[QueryParam("status")],
        ),
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_invoice,
            output_serializer=InvoiceSerializer,
            # An agent reading one invoice is not handed the bookkeeping
            # timestamp; the REST response is unchanged.
            field_audiences={"updated_at": FieldMarking.hidden()},
        ),
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

    @service_action(
        ANNOTATE_SPEC,
        detail=True,
        methods=["post"],
    )
    def annotate(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Set an invoice's customer and notes; off HTTP, ``partial=True`` sets either."""
