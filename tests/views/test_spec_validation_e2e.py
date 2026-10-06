"""End-to-end tests that ``as_view()`` trips spec validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q

from rest_framework_services import (
    Affordance,
    NotClientInput,
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    service_action,
)


@dataclass
class _AuthorIn:
    name: str


class TestServiceCreateViewValidation:
    def test_data_required_without_input_serializer_fails(self) -> None:
        def fn(*, data: _AuthorIn) -> None:
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn)

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            _View.as_view()

    def test_instance_required_in_create_fails(self) -> None:
        def fn(*, instance: object) -> None:
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn)

        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):
            _View.as_view()

    def test_unknown_required_kwarg_fails(self) -> None:
        def fn(*, tenant_id: int) -> None:
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn)

        with pytest.raises(ImproperlyConfigured, match="tenant_id"):
            _View.as_view()

    def test_unknown_kwarg_passes_when_spec_kwargs_supplied(self) -> None:
        def fn(*, tenant_id: int) -> None:
            return None

        def provider(view: Any, request: Any) -> dict[str, int]:
            return {"tenant_id": 1}

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn, kwargs=provider)

        _View.as_view()  # does not raise

    def test_unknown_kwarg_passes_when_get_service_kwargs_overridden(self) -> None:
        def fn(*, tenant_id: int) -> None:
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn)

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tenant_id": 1}

        _View.as_view()  # does not raise

    def test_output_selector_signature_validated(self) -> None:
        def fn(*, data: _AuthorIn) -> str:
            return data.name

        def selector(*, instance: object) -> str:
            return ""

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
            )

        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):
            _View.as_view()

    def test_no_spec_does_not_raise(self) -> None:
        # spec=None on the base class shouldn't crash as_view().
        ServiceCreateView.as_view()


class TestServiceUpdateViewValidation:
    def test_instance_required_passes(self) -> None:
        def fn(*, instance: object, data: _AuthorIn) -> None:
            return None

        class _View(ServiceUpdateView):
            spec = ServiceSpec(service=fn, input_serializer=_AuthorIn)

        _View.as_view()

    def test_unknown_kwarg_fails(self) -> None:
        def fn(*, instance: object, missing: int) -> None:
            return None

        class _View(ServiceUpdateView):
            spec = ServiceSpec(service=fn)

        with pytest.raises(ImproperlyConfigured, match="missing"):
            _View.as_view()

    def test_no_spec_does_not_raise(self) -> None:
        ServiceUpdateView.as_view()


class TestServiceDeleteViewValidation:
    def test_instance_required_passes(self) -> None:
        def fn(*, instance: object) -> None:
            return None

        class _View(ServiceDeleteView):
            spec = ServiceSpec(service=fn)

        _View.as_view()

    def test_data_required_without_input_serializer_fails(self) -> None:
        def fn(*, instance: object, data: _AuthorIn) -> None:
            return None

        class _View(ServiceDeleteView):
            spec = ServiceSpec(service=fn)

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            _View.as_view()

    def test_no_spec_does_not_raise(self) -> None:
        ServiceDeleteView.as_view()

    def test_output_selector_validated(self) -> None:
        def fn(*, instance: object) -> object:
            return None

        def selector(*, data: object) -> object:
            return None

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
            )

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            _View.as_view()


class TestSelectorViewValidation:
    def test_list_selector_with_data_fails(self) -> None:
        def fn(*, data: object) -> Any:
            return []

        class _View(SelectorListView):
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=fn)

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            _View.as_view()

    def test_retrieve_selector_with_instance_fails(self) -> None:
        def fn(*, instance: object) -> Any:
            return None

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=fn)

        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):
            _View.as_view()

    def test_retrieve_selector_with_unknown_kwarg_passes_due_to_url_kwargs(self) -> None:
        # URL kwargs are dynamic; the validator can't know what's available, so
        # selectors are permissive on unknown names.
        def fn(*, pk: int, tenant_id: int) -> Any:
            return None

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=fn)

        _View.as_view()

    def test_no_spec_does_not_raise(self) -> None:
        SelectorListView.as_view()
        SelectorRetrieveView.as_view()

    def test_spec_with_no_selector_does_not_raise(self) -> None:
        class _View(SelectorListView):
            spec = SelectorSpec(kind=SelectorKind.LIST)

        _View.as_view()

    def test_kind_mismatch_at_mount_point_fails(self) -> None:
        """A RETRIEVE spec on SelectorListView (or LIST on retrieve) is fail-fast."""

        class _List(SelectorListView):
            spec = SelectorSpec(kind=SelectorKind.RETRIEVE)

        with pytest.raises(ImproperlyConfigured, match=r"spec.kind"):
            _List.as_view()

        class _Retrieve(SelectorRetrieveView):
            spec = SelectorSpec(kind=SelectorKind.LIST)

        with pytest.raises(ImproperlyConfigured, match=r"spec.kind"):
            _Retrieve.as_view()

    def test_action_kind_mismatch_in_viewset_fails(self) -> None:
        """In a viewset, the "list" / "retrieve" entries must carry the matching kind."""

        class _View(ServiceViewSet):
            action_specs = {"list": SelectorSpec(kind=SelectorKind.RETRIEVE)}

        with pytest.raises(ImproperlyConfigured, match=r"spec.kind"):
            _View.as_view({"get": "list"})

    def test_output_selector_spec_list_kind_requires_collection(self) -> None:
        """output_selector_spec.kind=LIST is only valid alongside collection_selector_spec."""

        def fn(**_: Any) -> None:
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(kind=SelectorKind.LIST),
            )

        with pytest.raises(ImproperlyConfigured, match=r"output_selector_spec.kind=LIST"):
            _View.as_view()


class TestServiceViewSetValidation:
    def test_create_with_instance_required_fails(self) -> None:
        def fn(*, instance: object, data: _AuthorIn) -> None:
            return None

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(service=fn, input_serializer=_AuthorIn),
            }

        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):
            _View.as_view({"post": "create"})

    def test_update_with_instance_passes(self) -> None:
        def fn(*, instance: object, data: _AuthorIn) -> None:
            return None

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(service=fn, input_serializer=_AuthorIn),
            }

        _View.as_view({"put": "update"})

    def test_unknown_kwarg_passes_when_per_action_hook_present(self) -> None:
        def fn(*, data: _AuthorIn, tenant_id: int) -> None:
            return None

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(service=fn, input_serializer=_AuthorIn),
            }

            def get_create_service_kwargs(self) -> dict[str, Any]:
                return {"tenant_id": 1}

        _View.as_view({"post": "create"})

    def test_unknown_kwarg_fails_without_overrides(self) -> None:
        def fn(*, data: _AuthorIn, tenant_id: int) -> None:
            return None

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(service=fn, input_serializer=_AuthorIn),
            }

        with pytest.raises(ImproperlyConfigured, match="tenant_id"):
            _View.as_view({"post": "create"})

    def test_list_selector_invalid_signature_fails(self) -> None:
        def fn(*, data: object) -> Any:
            return []

        class _View(ServiceViewSet):
            action_specs = {"list": SelectorSpec(kind=SelectorKind.LIST, selector=fn)}

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            _View.as_view({"get": "list"})


class TestServiceActionValidation:
    def test_data_required_without_input_serializer_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="requires `data`"):

            def fn(*, data: _AuthorIn) -> None:
                return None

            class _View:  # noqa: D401
                @service_action(ServiceSpec(service=fn), detail=False, methods=["post"])
                def go(self, request):  # type: ignore[no-untyped-def]
                    ...

    def test_instance_required_on_non_detail_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):

            def fn(*, instance: object) -> None:
                return None

            class _View:  # noqa: D401
                @service_action(ServiceSpec(service=fn), detail=False, methods=["post"])
                def go(self, request):  # type: ignore[no-untyped-def]
                    ...

    def test_detail_action_with_instance_passes(self) -> None:
        def fn(*, instance: object) -> None:
            return None

        class _View:  # noqa: D401
            @service_action(ServiceSpec(service=fn), detail=True, methods=["post"])
            def go(self, request, pk=None):  # type: ignore[no-untyped-def]
                ...

    def test_output_selector_validated(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="requires `data`"):

            def fn() -> int:
                return 1

            def selector(*, data: object) -> object:
                return None

            class _View:  # noqa: D401
                @service_action(
                    ServiceSpec(
                        service=fn,
                        output_selector_spec=SelectorSpec(
                            kind=SelectorKind.RETRIEVE, selector=selector
                        ),
                    ),
                    detail=False,
                    methods=["post"],
                )
                def go(self, request):  # type: ignore[no-untyped-def]
                    ...


# Module level, so ``from __future__ import annotations`` leaves every hint below
# resolvable: a hint that cannot be resolved is read only as far as it evaluates
# (see test_schema_markers_beside_unresolved_annotations.py).
def _misplaced(*, team: list[Annotated[int, NotClientInput]] | None = None) -> list[Any]:
    """A marker inside a container: read by nothing, refused wherever it is read."""
    return []


def _in_the_one_optional(*, team: Annotated[int, NotClientInput] | None = None) -> None:
    return None


def _plain() -> list[Any]:
    return []


def _misplaced_affordance() -> Affordance:
    return Affordance(code="closed", reason="Closed.", when=_misplaced)


class _NotedQ(Q):
    """A condition on the row whose class carries an annotation.

    Reading it as a callable gets past resolving its hints, as far as asking for
    the signature of an object that has none, and fails there.
    """

    note: str


def _schema_marker_refusal(where: str) -> str:
    return rf"^{re.escape(where)}: .*a schema marker \(InputRequired, NotClientInput"


class TestMisplacedSchemaMarkerValidation:
    """A schema marker the dispatch read refuses is refused by ``as_view()``.

    Dispatch reads every callable's markers on every call, so a misplaced one left
    to it is an endpoint that fails each request rather than an app that fails to
    start.
    """

    def test_a_mutation_view_refuses_it(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(service=_misplaced)

        with pytest.raises(
            ImproperlyConfigured, match=_schema_marker_refusal("_View.spec.service")
        ):
            _View.as_view()

    def test_a_selector_view_refuses_it(self) -> None:
        class _View(SelectorListView):
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=_misplaced)

        with pytest.raises(
            ImproperlyConfigured, match=_schema_marker_refusal("_View.spec.selector")
        ):
            _View.as_view()

    def test_a_viewset_refuses_it(self) -> None:
        class _View(ServiceViewSet):
            action_specs = {"create": ServiceSpec(service=_misplaced)}

        with pytest.raises(
            ImproperlyConfigured,
            match=_schema_marker_refusal("_View.action_specs['create'].service"),
        ):
            _View.as_view({"post": "create"})

    @pytest.mark.parametrize(
        ("fields", "where"),
        [
            ({"preconditions": [_misplaced]}, "preconditions[0]"),
            (
                {
                    "instance_selector_spec": SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_misplaced
                    )
                },
                "instance_selector_spec.selector",
            ),
            (
                {
                    "collection_selector_spec": SelectorSpec(
                        kind=SelectorKind.LIST, selector=_misplaced
                    )
                },
                "collection_selector_spec.selector",
            ),
            (
                {
                    "output_selector_spec": SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_misplaced
                    )
                },
                "output_selector_spec.selector",
            ),
            ({"affordances": [_misplaced_affordance()]}, "affordances[0].when"),
        ],
        ids=["precondition", "instance-lookup", "collection-lookup", "output-re-read", "when"],
    )
    def test_every_callable_a_service_dispatch_reads_is_checked(
        self, fields: dict[str, Any], where: str
    ) -> None:
        class _View(ServiceUpdateView):
            spec = ServiceSpec(service=_plain, **fields)

        with pytest.raises(
            ImproperlyConfigured, match=_schema_marker_refusal(f"_View.spec.{where}")
        ):
            _View.as_view()

    @pytest.mark.parametrize(
        ("fields", "where"),
        [
            ({"preconditions": [_misplaced]}, "preconditions[0]"),
            (
                {
                    "affordances": {
                        "close": ServiceSpec(service=_plain, affordances=[_misplaced_affordance()])
                    }
                },
                "affordances['close'].affordances[0].when",
            ),
        ],
        ids=["precondition", "row-affordance-when"],
    )
    def test_every_callable_a_selector_dispatch_reads_is_checked(
        self, fields: dict[str, Any], where: str
    ) -> None:
        class _View(SelectorListView):
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=_plain, **fields)

        with pytest.raises(
            ImproperlyConfigured, match=_schema_marker_refusal(f"_View.spec.{where}")
        ):
            _View.as_view()

    def test_a_condition_on_the_row_is_not_read(self) -> None:
        # Compiled into the query rather than called, so no pool binds it and it
        # carries no markers to read.
        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=_plain,
                affordances=[Affordance(code="open", reason="Open.", when=_NotedQ(title="a"))],
            )

        _View.as_view()

    def test_a_marker_inside_the_one_optional_is_accepted(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(service=_in_the_one_optional)

        _View.as_view()
