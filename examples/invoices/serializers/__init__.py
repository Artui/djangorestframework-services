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


MIN_NET_DAYS = 1
MAX_NET_DAYS = 90


class NetDaysField(serializers.IntegerField):
    """Days an invoice stays open for payment.

    It declares no ``min_value`` or ``max_value``, so nothing in the class tells
    a schema its range. ``PAYMENT_TERMS_SPEC`` states the range as a rule for
    this class in its own registry, and that is what the schema, the compiled
    spec and the checks that run off HTTP read.
    """


class PaymentTermsInput(serializers.Serializer):
    """Input for ``terms``: how many days the customer has to pay."""

    days = NetDaysField()

    def validate_days(self, value: int) -> int:
        # The route's own check: over HTTP the serializer is the validator and
        # reads no registry. The rule states the same range for what a schema
        # shows, and for the compiled spec, which checks it before the
        # serializer runs.
        if not MIN_NET_DAYS <= value <= MAX_NET_DAYS:
            raise serializers.ValidationError(
                f"Payment terms run {MIN_NET_DAYS} to {MAX_NET_DAYS} days."
            )
        return value


class InvoiceFieldsSerializer(InvoiceSerializer):
    """``InvoiceSerializer`` that keeps the fields a caller's ``?fields=`` names.

    It reads the request the serializer context holds. Off HTTP the kernel's
    presenter builds an offline one whose query string is the call's shaping
    arguments, which is what ``FIELDS_SPEC`` is run with.
    """

    def to_representation(self, instance: Invoice) -> dict[str, object]:
        data = super().to_representation(instance)
        request = self.context.get("request")
        wanted = request.query_params.get("fields") if request is not None else None
        if not wanted:
            return data
        return {name: data[name] for name in wanted.split(",") if name in data}
