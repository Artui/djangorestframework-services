"""End-to-end test of the invoices flow.

Exercises every endpoint exposed by ``InvoiceViewSet`` (including the
custom ``@service_action`` decorator) using DRF's APIClient. Run with::

    python manage.py test invoices
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from invoices.models import Invoice
from invoices.views import ANNOTATE_SPEC, InvoiceViewSet
from rest_framework_services import FieldMarking, QueryParam, dispatch_spec


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


class DeclaredInputsTests(APITestCase):
    """What the specs declare beyond the serializer, and the same specs off HTTP."""

    client_class = APIClient

    def setUp(self) -> None:
        self.draft = Invoice.objects.create(customer="Acme", amount_cents=1, notes="Net 15")
        Invoice.objects.create(customer="Initech", amount_cents=2, status="paid")

    def test_a_declared_read_reaches_the_selector_by_name(self) -> None:
        spec = InvoiceViewSet.action_specs["list"]
        self.assertEqual(spec.agent_contract().query_params, (QueryParam("status"),))

        # Over HTTP from the query string, off HTTP as an argument.
        response = self.client.get("/invoices/?status=paid")
        self.assertEqual([row["customer"] for row in response.data], ["Initech"])
        result = dispatch_spec(spec, user=AnonymousUser(), params={"status": "draft"})
        self.assertEqual([row.customer for row in result.value], ["Acme"])

    def test_field_audiences_hide_a_field_from_an_agent_only(self) -> None:
        contract = InvoiceViewSet.action_specs["retrieve"].agent_contract()
        self.assertEqual(contract.field_audiences, {"updated_at": FieldMarking.hidden()})

        response = self.client.get(f"/invoices/{self.draft.pk}/")
        self.assertIn("updated_at", response.data)

    def test_a_partial_call_off_http_validates_only_what_it_sends(self) -> None:
        spec = ANNOTATE_SPEC

        # A whole call needs both fields, as the POST route does.
        with self.assertRaises(ValidationError):
            dispatch_spec(
                spec, user=AnonymousUser(), instance=self.draft, params={"notes": "Net 30"}
            )

        dispatch_spec(
            spec,
            user=AnonymousUser(),
            instance=self.draft,
            params={"notes": "Net 30"},
            partial=True,
        )
        self.draft.refresh_from_db()
        self.assertEqual((self.draft.customer, self.draft.notes), ("Acme", "Net 30"))

    def test_the_annotate_route_takes_a_whole_call(self) -> None:
        response = self.client.post(
            f"/invoices/{self.draft.pk}/annotate/",
            {"customer": "Acme Ltd", "notes": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["customer"], response.data["notes"]), ("Acme Ltd", ""))
