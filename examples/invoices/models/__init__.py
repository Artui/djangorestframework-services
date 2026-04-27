"""Invoice model — single resource so the example stays focused."""

from __future__ import annotations

from django.db import models


class Invoice(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_PAID = "paid"
    STATUS_VOID = "void"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENT, "Sent"),
        (STATUS_PAID, "Paid"),
        (STATUS_VOID, "Void"),
    )

    customer = models.CharField(max_length=200)
    amount_cents = models.PositiveIntegerField()
    notes = models.TextField(default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        app_label = "invoices"
