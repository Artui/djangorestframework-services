"""End-to-end test of the invoices flow.

Exercises every endpoint exposed by ``InvoiceViewSet`` (including the
custom ``@service_action`` decorator) using DRF's APIClient. Run with::

    python manage.py test invoices
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django_service_specs import Grant, dispatch, kernel_spec
from django_service_specs import InvalidArguments as KernelInvalidArguments
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from invoices.models import Invoice
from invoices.serializers import InvoiceSerializer
from invoices.views import ANNOTATE_SPEC, PAYMENT_TERMS_SPEC, InvoiceViewSet
from rest_framework_services import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    FieldAudience,
    FieldMarking,
    QueryParam,
    build_audience_projection,
    dispatch_spec,
    spec_to_json_schema,
)


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

        # What an agent transport builds from it: the field is dropped there.
        projection = build_audience_projection(
            InvoiceSerializer, overrides=contract.field_audiences, name="get_invoice"
        )
        self.assertEqual(projection.audience("updated_at"), FieldAudience.HIDDEN)
        self.assertEqual(projection.audience("customer"), FieldAudience.CONTENT)

        response = self.client.get(f"/invoices/{self.draft.pk}/")
        self.assertIn("updated_at", response.data)

    def test_a_partial_call_off_http_validates_only_what_it_sends(self) -> None:
        spec = ANNOTATE_SPEC

        # A whole call needs both fields, as the POST route does.
        with self.assertRaises(ValidationError) as refused:
            dispatch_spec(
                spec, user=AnonymousUser(), instance=self.draft, params={"notes": "Net 30"}
            )
        self.assertIn("customer", refused.exception.detail)

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
            f"/invoices/{self.draft.pk}/annotate/", {"notes": "Net 30"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("customer", response.data)

        response = self.client.post(
            f"/invoices/{self.draft.pk}/annotate/",
            {"customer": "Acme Ltd", "notes": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["customer"], response.data["notes"]), ("Acme Ltd", ""))


class SpecRegistryTests(APITestCase):
    """The registry ``PAYMENT_TERMS_SPEC`` declares: one rule, three readers.

    ``NetDaysField`` carries no bounds, so every bound the schema and the
    compiled spec state comes from the rule and none from the field.
    """

    client_class = APIClient

    def setUp(self) -> None:
        self.invoice = Invoice.objects.create(customer="Acme", amount_cents=1)

    def test_the_schema_carries_the_rule_with_no_registry_argument(self) -> None:
        days = spec_to_json_schema(PAYMENT_TERMS_SPEC)["properties"]["days"]
        self.assertEqual(days, {"type": "integer", "format": "days", "minimum": 1, "maximum": 90})

        # A registry handed to the helper wins, the empty default included.
        bare = spec_to_json_schema(PAYMENT_TERMS_SPEC, registry=DEFAULT_JSON_SCHEMA_REGISTRY)
        self.assertEqual(bare["properties"]["days"], {"type": "integer"})

    def test_the_compiled_spec_keeps_what_the_kernel_can_state_and_drops_the_rest(self) -> None:
        days = kernel_spec(PAYMENT_TERMS_SPEC).parameters().get("days")
        self.assertEqual((days.minimum, days.maximum), (1, 90))

        # "days" is none of the kernel's four formats, so it is dropped there
        # while drfs' own schema keeps it.
        self.assertIsNone(days.format)
        schema = spec_to_json_schema(PAYMENT_TERMS_SPEC)
        self.assertEqual(schema["properties"]["days"]["format"], "days")

    def test_the_kernel_refuses_a_value_outside_the_rule_s_bounds(self) -> None:
        compiled = kernel_spec(PAYMENT_TERMS_SPEC)
        principal = AnonymousUser()

        def call(days: int) -> object:
            return dispatch(
                compiled,
                principal=principal,
                arguments={"pk": self.invoice.pk, "days": days},
                grant=Grant(compiled, principal),
            )

        # The kernel's own bound messages: the serializer's ``validate_days``
        # words its refusal differently, so these came from the rule.
        for days, message in (
            (0, "Ensure this value is greater than or equal to 1."),
            (91, "Ensure this value is less than or equal to 90."),
        ):
            with self.assertRaises(KernelInvalidArguments) as refused:
                call(days)
            self.assertEqual(refused.exception.detail, {"days": [message]})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.notes, "")

        call(30)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.notes, "[terms] net 30")

    def test_the_route_answers_the_same_range_from_the_serializer(self) -> None:
        # Over HTTP the serializer is the validator, so this 400 is
        # ``validate_days``, not the rule; the rule is what the two tests above
        # hold for the schema and the compiled spec.
        url = f"/invoices/{self.invoice.pk}/terms/"
        response = self.client.post(url, {"days": 0}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("days", response.data)

        response = self.client.post(url, {"days": 30}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["notes"], "[terms] net 30")
