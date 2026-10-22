"""Viewset wiring services and selectors to routes."""

from __future__ import annotations

from invoices.models import Invoice
from invoices.selectors import get_invoice, list_invoices
from invoices.serializers import (
    MAX_NET_DAYS,
    MIN_NET_DAYS,
    AnnotateInvoiceInput,
    InvoiceSerializer,
    NetDaysField,
    PaymentTermsInput,
)
from invoices.services import (
    ApproveInput,
    CreateInvoiceInput,
    UpdateInvoiceInput,
    annotate_invoice,
    create_invoice,
    delete_invoice,
    mark_invoice_sent,
    set_payment_terms,
    update_invoice,
)
from rest_framework_services import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
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

# The one statement of what a ``NetDaysField`` is, declared on the spec that
# takes one. ``type``, ``minimum`` and ``maximum`` are keywords the kernel's
# parameters spell, so the compiled spec checks them off HTTP; ``days`` is none
# of the kernel's formats, so only drfs' own schema keeps it.
PAYMENT_TERMS_REGISTRY = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
    fields=[
        (
            NetDaysField,
            {"type": "integer", "format": "days", "minimum": MIN_NET_DAYS, "maximum": MAX_NET_DAYS},
        )
    ],
)

PAYMENT_TERMS_SPEC = ServiceSpec(
    service=set_payment_terms,
    input_serializer=PaymentTermsInput,
    # The row is found by the same selector the retrieve route reads, so the
    # kernel's own dispatch can resolve it from a ``pk`` argument.
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=get_invoice),
    output_selector_spec=_invoice_response_spec(),
    json_schema_registry=PAYMENT_TERMS_REGISTRY,
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

    @service_action(
        PAYMENT_TERMS_SPEC,
        detail=True,
        methods=["post"],
    )
    def terms(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Record the days an invoice stays open; the spec's registry bounds them."""
