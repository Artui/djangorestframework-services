"""Output serializers for the invoices app."""

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
