"""Selector callables — plain functions returning data."""

from __future__ import annotations

from typing import Any

from invoices.models import Invoice


def list_invoices(*, request: Any) -> Any:
    """Return all invoices, newest first; supports a ``?status=`` filter."""
    queryset = Invoice.objects.all()
    status = request.query_params.get("status")
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def get_invoice(*, pk: int) -> Invoice | None:
    return Invoice.objects.filter(pk=pk).first()
