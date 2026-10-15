"""Selector callables — plain functions returning data."""

from __future__ import annotations

from typing import Any

from invoices.models import Invoice


def list_invoices(*, status: str | None = None) -> Any:
    """Return all invoices, newest first; supports a ``?status=`` filter.

    ``status`` is the ``QueryParam`` the list spec declares in its ``reads``,
    handed over by name: from the query string on the live view, and as an
    argument off HTTP. It keeps a default so a list with no ``?status=``
    answers every invoice; a required one would answer it 400.
    """
    queryset = Invoice.objects.all()
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def get_invoice(*, pk: int) -> Invoice | None:
    return Invoice.objects.filter(pk=pk).first()
