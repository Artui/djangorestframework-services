"""Service callables — plain functions, no DRF imports.

The library does not prescribe a signature; views inspect the signature
and pass only what's declared. We use keyword-only args throughout for
clarity.
"""

from __future__ import annotations

from dataclasses import dataclass

from invoices.models import Invoice
from rest_framework_services import (
    ServiceError,
    ServiceValidationError,
    create_from_input,
    update_from_input,
)

# -----------------------------------------------------------------------
# Input dataclasses.
#
# Update inputs use ``Optional[T]`` defaults; the service filters out
# ``None`` values before applying. For the stricter "distinguish between
# omitted and ``None``" pattern, use the library's ``UNSET`` sentinel —
# see the README's "Mutation helpers" section.
# -----------------------------------------------------------------------


@dataclass
class CreateInvoiceInput:
    customer: str
    amount_cents: int
    notes: str = ""


@dataclass
class UpdateInvoiceInput:
    customer: str | None = None
    amount_cents: int | None = None
    notes: str | None = None


@dataclass
class ApproveInput:
    note: str = ""


# -----------------------------------------------------------------------
# Services
# -----------------------------------------------------------------------


def create_invoice(*, data: CreateInvoiceInput) -> Invoice:
    if data.amount_cents <= 0:
        raise ServiceValidationError({"amount_cents": ["must be positive"]})
    result = create_from_input(Invoice, data)
    return result.instance


def update_invoice(*, instance: Invoice, data: UpdateInvoiceInput) -> Invoice:
    if instance.status == Invoice.STATUS_PAID:
        raise ServiceError("paid invoices cannot be edited")
    fields = {key: value for key, value in vars(data).items() if value is not None}
    result = update_from_input(instance, fields)
    return result.instance


def delete_invoice(*, instance: Invoice) -> None:
    if instance.status == Invoice.STATUS_PAID:
        raise ServiceError("paid invoices cannot be deleted")
    instance.delete()


def mark_invoice_sent(*, instance: Invoice, data: ApproveInput) -> Invoice:
    """Custom action: flip an invoice to ``sent`` and append a note."""
    if instance.status != Invoice.STATUS_DRAFT:
        raise ServiceValidationError(
            {"status": [f"can only send drafts; this invoice is {instance.status}"]},
        )
    instance.status = Invoice.STATUS_SENT
    if data.note:
        instance.notes = (
            f"{instance.notes}\n[sent] {data.note}".strip()
            if instance.notes
            else f"[sent] {data.note}"
        )
    instance.save(update_fields=["status", "notes", "updated_at"])
    return instance
