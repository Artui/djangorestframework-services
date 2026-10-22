"""End-to-end test of the invoices flow.

Exercises every endpoint exposed by ``InvoiceViewSet`` (including the
custom ``@service_action`` decorator) using DRF's APIClient. Run with::

    python manage.py test invoices
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django_service_specs import Grant, dispatch, kernel_spec, present
from django_service_specs import InvalidArguments as KernelInvalidArguments
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.test import APIClient, APITestCase

from invoices.models import Invoice
from invoices.selectors import list_invoices
from invoices.serializers import InvoiceSerializer
from invoices.views import (
    ANNOTATE_SPEC,
    FIELDS_SPEC,
    PAYMENT_TERMS_SPEC,
    RECORD_OPERATION_SPEC,
    VOID_DRAFTS_SPEC,
    VOID_DRAFTS_SUMMARY_SPEC,
    InvoiceViewSet,
    operation_from_context,
)
from rest_framework_services import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    FieldAudience,
    FieldMarking,
    OfflineServiceView,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    build_audience_projection,
    build_offline_context,
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
        self.assertEqual(response.data, {"days": ["Payment terms run 1 to 90 days."]})

        response = self.client.post(url, {"days": 30}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["notes"], "[terms] net 30")


class CallContextTests(APITestCase):
    """The dict a call carries: the route's own, and one handed to ``dispatch_spec`` off HTTP."""

    client_class = APIClient

    def setUp(self) -> None:
        self.invoice = Invoice.objects.create(customer="Acme", amount_cents=1)

    def test_a_provider_naming_call_context_reads_the_dict_the_caller_passed(self) -> None:
        seen: dict[str, Any] = {}
        user = AnonymousUser()
        context = build_offline_context(user, {})
        # A view with a name for the operation: with none, ``action`` is ``None``.
        view = OfflineServiceView(request=context.request, action="reconcile")
        call_context: dict[str, Any] = {"trace_id": "t-1"}

        def spy(*, call_context: dict[str, Any]) -> dict[str, str]:
            seen["context"] = call_context
            return operation_from_context(call_context=call_context)

        spec = ServiceSpec(
            service=RECORD_OPERATION_SPEC.service,
            kwargs=spy,
            output_selector_spec=RECORD_OPERATION_SPEC.output_selector_spec,
        )
        dispatch_spec(
            spec,
            user=user,
            instance=self.invoice,
            params={},
            request=context.request,
            view=view,
            call_context=call_context,
        )

        # The provider was handed the caller's dict itself, not a copy ...
        self.assertIs(seen["context"], call_context)
        # ... which the edge filled with the four standard entries and left the
        # caller's own entry in.
        self.assertIs(call_context["request"], context.request)
        self.assertIs(call_context["view"], view)
        self.assertIsNone(call_context["format"])
        self.assertEqual(call_context["action"], "reconcile")
        self.assertEqual(call_context["trace_id"], "t-1")
        # What the provider read from it is what the service wrote.
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.notes, "[operation] reconcile")

    def test_over_http_the_provider_reads_the_route_s_action(self) -> None:
        # Routed, the view's call context carries the viewset's action name.
        response = self.client.post(f"/invoices/{self.invoice.pk}/record/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["notes"], "[operation] record")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.notes, "[operation] record")

    def test_with_no_view_the_action_is_none(self) -> None:
        # No view, so the edge's ``action`` entry is ``None`` and the provider says so.
        dispatch_spec(RECORD_OPERATION_SPEC, user=AnonymousUser(), instance=self.invoice, params={})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.notes, "[operation] unnamed")


class ShapingQueryParamTests(APITestCase):
    """``QueryParam("fields")`` that no callable names shapes the output, not the run."""

    def setUp(self) -> None:
        Invoice.objects.create(customer="Acme", amount_cents=4200)
        self.compiled = kernel_spec(FIELDS_SPEC)

    def test_the_compiled_spec_holds_it_as_a_shape_and_not_a_read(self) -> None:
        # ``list_invoices`` takes ``status`` and nothing else, so ``status``
        # runs the selector and ``fields`` only shapes what is presented.
        self.assertEqual([param.name for param in self.compiled.shapes], ["fields"])
        self.assertEqual([param.name for param in self.compiled.reads], ["status"])
        # Both are still arguments a caller may send.
        self.assertEqual(set(self.compiled.parameters().names()), {"status", "fields"})

    def test_the_shape_reaches_the_serializer_off_http(self) -> None:
        principal = AnonymousUser()

        shaped = dispatch(self.compiled, principal=principal, arguments={"fields": "customer"})
        self.assertEqual(present(self.compiled, shaped), [{"customer": "Acme"}])

        # Without it the same call answers every field: the serializer, not the
        # spec, drops them.
        whole = dispatch(self.compiled, principal=principal, arguments={})
        (row,) = present(self.compiled, whole)
        self.assertEqual(set(row), set(InvoiceSerializer.Meta.fields))


class ReturnsManyTests(APITestCase):
    """A service whose list output re-reads nothing: its return is the rows."""

    def setUp(self) -> None:
        self.draft = Invoice.objects.create(customer="Acme", amount_cents=1)
        Invoice.objects.create(customer="Paid", amount_cents=2, status=Invoice.STATUS_PAID)

    def test_the_compiled_spec_returns_many(self) -> None:
        compiled = kernel_spec(VOID_DRAFTS_SPEC)
        self.assertTrue(compiled.returns_many)
        # The service is the only source of rows: nothing is read again.
        self.assertIsNone(compiled.output_selector_spec)

    def test_the_kernel_answers_and_presents_a_list(self) -> None:
        compiled = kernel_spec(VOID_DRAFTS_SPEC)

        result = dispatch(compiled, principal=AnonymousUser(), arguments={})

        self.assertEqual(result.kind, "list")
        rows = present(compiled, result)
        self.assertEqual([(row["customer"], row["status"]) for row in rows], [("Acme", "void")])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, Invoice.STATUS_VOID)

    def test_the_edge_refuses_a_mapping_where_rows_are_declared(self) -> None:
        with self.assertRaisesMessage(ImproperlyConfigured, "declares kind=LIST with no selector"):
            dispatch_spec(VOID_DRAFTS_SUMMARY_SPEC, user=AnonymousUser(), params={})

        # The author's error, raised after the write: the drafts are voided.
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, Invoice.STATUS_VOID)


class _Asked(BasePermission):
    """Records the request and view each permission question was asked against."""

    asked: list[tuple[Any, Any]] = []

    def has_permission(self, request: Any, view: Any) -> bool:
        self.asked.append((request, view))
        return True


class CarriedRequestTests(APITestCase):
    """The request a run arrived on, carried in the call's context."""

    def setUp(self) -> None:
        _Asked.asked.clear()
        users = get_user_model().objects
        self.principal = users.create_user("ada")
        self.arrived_user = users.create_user("bob")
        self.spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=InvoiceSerializer,
            permission_classes=[_Asked],
            reads=[QueryParam("status")],
        )

    def test_a_permission_class_sees_the_principal_and_the_call_s_query_params(self) -> None:
        # The run arrived on another endpoint, as another user, with its own
        # query string and headers.
        arrived = RequestFactory().get("/chat/", {"status": "paid"}, HTTP_X_CLIENT="chat")
        arrived.user = self.arrived_user

        dispatch(
            kernel_spec(self.spec),
            principal=self.principal,
            arguments={"status": "sent"},
            call_context={"request": arrived},
        )

        ((request, view),) = _Asked.asked
        # Carried: the check was handed this request's headers ...
        self.assertEqual(request.META["HTTP_X_CLIENT"], "chat")
        # ... but under the principal and the operation's own query string.
        self.assertEqual(request.user, self.principal)
        self.assertEqual(dict(request.query_params.lists()), {"status": ["sent"]})
        self.assertIs(view.request, request)
        # It read a copy: the request the run arrived on is untouched.
        self.assertEqual(arrived.user, self.arrived_user)
        self.assertEqual(dict(arrived.GET.lists()), {"status": ["paid"]})
