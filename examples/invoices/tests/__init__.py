"""End-to-end test of the invoices flow.

Exercises every endpoint exposed by ``InvoiceViewSet`` (including the
custom ``@service_action`` decorator) using DRF's APIClient. Run with::

    python manage.py test invoices
"""

from __future__ import annotations

from rest_framework.test import APIClient, APITestCase

from invoices.models import Invoice


class InvoiceFlowTests(APITestCase):
    client_class = APIClient

    def test_create_list_retrieve_update_send_delete_flow(self) -> None:
        # CREATE
        response = self.client.post(
            "/invoices/",
            {"customer": "Acme", "amount_cents": 4200, "notes": "Thanks!"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        invoice_id = response.data["id"]
        self.assertEqual(response.data["status"], "draft")

        # LIST
        response = self.client.get("/invoices/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        # LIST with filter
        response = self.client.get("/invoices/?status=draft")
        self.assertEqual(len(response.data), 1)
        response = self.client.get("/invoices/?status=paid")
        self.assertEqual(len(response.data), 0)

        # RETRIEVE
        response = self.client.get(f"/invoices/{invoice_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["customer"], "Acme")

        # PATCH (partial update)
        response = self.client.patch(
            f"/invoices/{invoice_id}/",
            {"notes": "Updated"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["notes"], "Updated")

        # SEND (custom @service_action)
        response = self.client.post(
            f"/invoices/{invoice_id}/send/",
            {"note": "via API"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "sent")
        self.assertIn("[sent] via API", response.data["notes"])

        # SEND again should 400 (only drafts can be sent)
        response = self.client.post(f"/invoices/{invoice_id}/send/", {}, format="json")
        self.assertEqual(response.status_code, 400)

        # DELETE
        response = self.client.delete(f"/invoices/{invoice_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Invoice.objects.filter(pk=invoice_id).exists())

    def test_create_with_negative_amount_returns_400(self) -> None:
        response = self.client.post(
            "/invoices/",
            {"customer": "Acme", "amount_cents": -1},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount_cents", response.data)

    def test_paid_invoice_cannot_be_deleted(self) -> None:
        invoice = Invoice.objects.create(customer="Acme", amount_cents=1, status="paid")
        response = self.client.delete(f"/invoices/{invoice.pk}/")
        self.assertEqual(response.status_code, 422)
