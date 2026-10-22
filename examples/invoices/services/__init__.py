"""Service callables — plain functions, no DRF imports.

The library does not prescribe a signature; views inspect the signature
and pass only what's declared. We use keyword-only args throughout for
clarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def annotate_invoice(*, instance: Invoice, data: dict[str, str]) -> Invoice:
    """Custom action: apply whichever of ``customer`` / ``notes`` the call validated.

    A partial call validates only the fields it sends, so ``data`` holds
    exactly those.
    """
    return update_from_input(instance, data).instance


def set_payment_terms(*, instance: Invoice, data: dict[str, int]) -> Invoice:
    """Custom action: record the days the customer has to pay, as a note."""
    line = f"[terms] net {data['days']}"
    instance.notes = f"{instance.notes}\n{line}".strip()
    instance.save(update_fields=["notes", "updated_at"])
    return instance


def record_operation(*, instance: Invoice, operation: str) -> Invoice:
    """Custom action: note which operation touched the invoice.

    ``operation`` is no input and no URL capture: the spec's ``kwargs`` provider
    reads it off the call's context.
    """
    line = f"[operation] {operation}"
    instance.notes = f"{instance.notes}\n{line}".strip()
    instance.save(update_fields=["notes", "updated_at"])
    return instance


def void_drafts() -> list[Invoice]:
    """Void every draft and return the rows it voided.

    The spec that runs it declares a list output with no selector, so the rows
    returned here are the rows presented: nothing is read again.
    """
    drafts = list(Invoice.objects.filter(status=Invoice.STATUS_DRAFT))
    for invoice in drafts:
        invoice.status = Invoice.STATUS_VOID
        invoice.save(update_fields=["status", "updated_at"])
    return drafts


def void_drafts_summary() -> dict[str, Any]:
    """Void every draft but return a count, which is no set of rows.

    Run under the same list declaration as ``void_drafts``, the return is
    refused as the author's error, after the write.
    """
    return {"voided": len(void_drafts())}
