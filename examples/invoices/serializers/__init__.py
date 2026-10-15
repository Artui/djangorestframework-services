"""Serializers for the invoices app."""

from __future__ import annotations

from rest_framework import serializers

from invoices.models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = (
            "id",
            "customer",
            "amount_cents",
            "notes",
            "status",
            "created_at",
            "updated_at",
        )


class AnnotateInvoiceInput(serializers.Serializer):
    """Input for ``annotate``: a whole call sends both fields, a partial one either."""

    customer = serializers.CharField(max_length=200)
    notes = serializers.CharField(allow_blank=True)
