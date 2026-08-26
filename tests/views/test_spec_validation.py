"""Unit tests for the fail-fast spec-signature validator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission
from rest_framework.serializers import CharField, Serializer

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    SelectorViewSet,
    ServiceSpec,
)
from rest_framework_services.views.spec_validation import (
    _uses_django_filter_backend,
    is_overridden,
    validate_callable_signature,
    validate_filter_set_no_backend_conflict,
    validate_selector_spec,
    validate_service_spec,
)


@dataclass
class _InputDataclass:
    """A dataclass *type* is a legal ``input_serializer``; an instance is not."""

    name: str


class _InputSerializer(Serializer):
    name = CharField()


class _AlwaysAllow(BasePermission):
    """A well-formed permission class; never instantiated by the validator."""


class DjangoFilterBackend:
    """A no-op stand-in for django-filter's backend.

    The guard detects the real backend purely by class name (django-filter is
    an optional dep this package never imports), so a same-named stub is all
    the tests need.
    """

    def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
        return queryset


class _SubclassedFilterBackend(DjangoFilterBackend):
    """A project subclass of the real backend — the MRO name-walk must catch it."""


class _UnrelatedBackend:
    """A different backend that must not trip the guard."""

    def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
        return queryset


# Any non-None object satisfies the guard; it is never instantiated at
# ``as_view()`` time, only checked for presence.
_FILTER_SET = object()


def _selector() -> None:
    """A valid no-arg selector; never invoked (``as_view`` only validates)."""
    return None


class TestValidateCallableSignature:
    def test_var_keyword_passes(self) -> None:
        def fn(**kwargs: object) -> None:
            return None

        # Whatever the pool looks like, **kwargs cannot fail at dispatch.
        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=None,
            permissive_extras=False,
        )

    def test_optional_required_unknown_passes_when_default_set(self) -> None:
        def fn(*, request: object, x: int = 0) -> None:
            return None

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=None,
            permissive_extras=False,
        )

    def test_data_required_without_input_serializer_fails(self) -> None:
        def fn(*, data: object) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            validate_callable_signature(
                fn,
                spec_label="X",
                has_data=False,
                has_instance=False,
                has_result=False,
                spec_kwargs=None,
                permissive_extras=False,
            )

    def test_instance_required_when_action_has_no_instance_fails(self) -> None:
        def fn(*, instance: object) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="requires `instance`"):
            validate_callable_signature(
                fn,
                spec_label="X",
                has_data=False,
                has_instance=False,
                has_result=False,
                spec_kwargs=None,
                permissive_extras=False,
            )

    def test_result_required_when_not_output_selector_fails(self) -> None:
        def fn(*, result: object) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="requires `result`"):
            validate_callable_signature(
                fn,
                spec_label="X",
                has_data=False,
                has_instance=False,
                has_result=False,
                spec_kwargs=None,
                permissive_extras=False,
            )

    def test_required_unknown_param_fails_when_strict(self) -> None:
        def fn(*, request: object, tenant_id: int) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="tenant_id"):
            validate_callable_signature(
                fn,
                spec_label="X",
                has_data=False,
                has_instance=False,
                has_result=False,
                spec_kwargs=None,
                permissive_extras=False,
            )

    def test_required_unknown_param_passes_when_spec_kwargs_set(self) -> None:
        def fn(*, request: object, tenant_id: int) -> None:
            return None

        def provider(view: object, request: object) -> dict[str, int]:
            return {"tenant_id": 1}

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=provider,
            permissive_extras=False,
        )

    def test_required_unknown_param_passes_when_permissive(self) -> None:
        def fn(*, request: object, tenant_id: int) -> None:
            return None

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=None,
            permissive_extras=True,
        )

    def test_extra_known_keys_widen_allowed_set(self) -> None:
        def fn(*, pk: int) -> None:
            return None

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=None,
            permissive_extras=False,
            extra_known_keys=("pk",),
        )

    def test_data_instance_result_allowed_when_pool_provides_them(self) -> None:
        def fn(*, data: object, instance: object, result: object) -> None:
            return None

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=True,
            has_instance=True,
            has_result=True,
            spec_kwargs=None,
            permissive_extras=False,
        )

    def test_serializer_required_without_input_serializer_fails(self) -> None:
        def fn(*, serializer: object) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="requires `serializer`"):
            validate_callable_signature(
                fn,
                spec_label="X",
                has_data=False,
                has_instance=False,
                has_result=False,
                spec_kwargs=None,
                permissive_extras=False,
            )

    def test_serializer_allowed_when_input_serializer_present(self) -> None:
        def fn(*, serializer: object) -> None:
            return None

        validate_callable_signature(
            fn,
            spec_label="X",
            has_data=True,
            has_instance=False,
            has_result=False,
            spec_kwargs=None,
            permissive_extras=False,
        )


class TestValidateInstanceSelectorSpec:
    def _spec(self, instance_spec: SelectorSpec) -> ServiceSpec:
        def service(*, instance: object) -> None:
            return None

        return ServiceSpec(service=service, instance_selector_spec=instance_spec)

    def test_valid_spec_passes(self) -> None:
        validate_service_spec(
            self._spec(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: None)),
            label="X",
            has_instance=True,
            permissive_extras=False,
        )

    def test_list_kind_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be SelectorKind.RETRIEVE"):
            validate_service_spec(
                self._spec(SelectorSpec(kind=SelectorKind.LIST, selector=lambda *, pk: None)),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_fails_on_instanceless_action(self) -> None:
        spec = ServiceSpec(
            service=lambda: None,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: None
            ),
        )
        with pytest.raises(ImproperlyConfigured, match="does not target an instance"):
            validate_service_spec(spec, label="X", has_instance=False, permissive_extras=False)

    def test_shaping_without_selector_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="`selector` is not"):
            validate_service_spec(
                self._spec(SelectorSpec(kind=SelectorKind.RETRIEVE, select_related=("author",))),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_selector_requiring_data_fails(self) -> None:
        def lookup(*, data: object) -> None:
            return None

        with pytest.raises(ImproperlyConfigured, match="requires `data`"):
            validate_service_spec(
                self._spec(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lookup)),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_selector_none_passes_validation(self) -> None:
        # ``selector=None`` means "fall back to the view's get_object()
        # chain"; nothing to validate on the nested spec.
        validate_service_spec(
            self._spec(SelectorSpec(kind=SelectorKind.RETRIEVE)),
            label="X",
            has_instance=True,
            permissive_extras=False,
        )


class TestIsOverridden:
    def test_returns_false_when_method_is_inherited(self) -> None:
        class Base:
            def hook(self) -> int:
                return 0

        class Sub(Base): ...

        assert is_overridden(Sub, Base, "hook") is False

    def test_returns_true_when_subclass_overrides(self) -> None:
        class Base:
            def hook(self) -> int:
                return 0

        class Sub(Base):
            def hook(self) -> int:
                return 1

        assert is_overridden(Sub, Base, "hook") is True

    def test_missing_on_base_falls_back_to_hasattr(self) -> None:
        class Base: ...

        class WithHook(Base):
            def hook(self) -> int:
                return 1

        class WithoutHook(Base): ...

        assert is_overridden(WithHook, Base, "hook") is True
        assert is_overridden(WithoutHook, Base, "hook") is False


class TestUsesDjangoFilterBackend:
    def test_false_when_no_filter_backends(self) -> None:
        assert _uses_django_filter_backend(type("V", (), {})) is False

    def test_false_when_filter_backends_empty(self) -> None:
        assert _uses_django_filter_backend(type("V", (), {"filter_backends": []})) is False

    def test_true_for_django_filter_backend(self) -> None:
        view = type("V", (), {"filter_backends": [DjangoFilterBackend]})
        assert _uses_django_filter_backend(view) is True

    def test_true_for_subclass(self) -> None:
        view = type("V", (), {"filter_backends": [_SubclassedFilterBackend]})
        assert _uses_django_filter_backend(view) is True

    def test_false_for_unrelated_backend(self) -> None:
        view = type("V", (), {"filter_backends": [_UnrelatedBackend]})
        assert _uses_django_filter_backend(view) is False


class TestValidateFilterSetNoBackendConflict:
    def _list_spec(self, **kw: Any) -> SelectorSpec:
        return SelectorSpec(kind=SelectorKind.LIST, selector=_selector, **kw)

    def test_silent_when_no_filter_set(self) -> None:
        view = type("V", (), {"filter_backends": [DjangoFilterBackend]})
        validate_filter_set_no_backend_conflict(view, self._list_spec(), label="X")

    def test_silent_when_no_backend(self) -> None:
        view = type("V", (), {"filter_backends": []})
        validate_filter_set_no_backend_conflict(
            view, self._list_spec(filter_set=_FILTER_SET), label="X"
        )

    def test_raises_on_conflict(self) -> None:
        view = type("V", (), {"filter_backends": [DjangoFilterBackend]})
        with pytest.raises(ImproperlyConfigured, match="filter_set"):
            validate_filter_set_no_backend_conflict(
                view, self._list_spec(filter_set=_FILTER_SET), label="X"
            )


class TestFilterSetGuardAtAsView:
    """End-to-end: the guard fires through each ``as_view()`` mount point."""

    def test_list_view_raises_on_conflict(self) -> None:
        class _View(SelectorListView):
            filter_backends = [DjangoFilterBackend]
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=_selector, filter_set=_FILTER_SET)

        with pytest.raises(ImproperlyConfigured, match="filter_set"):
            _View.as_view()

    def test_list_view_silent_without_backend(self) -> None:
        class _View(SelectorListView):
            filter_backends = []  # type: ignore[var-annotated]
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=_selector, filter_set=_FILTER_SET)

        assert _View.as_view() is not None

    def test_list_view_silent_with_backend_but_no_filter_set(self) -> None:
        class _View(SelectorListView):
            filter_backends = [DjangoFilterBackend]
            spec = SelectorSpec(kind=SelectorKind.LIST, selector=_selector)

        assert _View.as_view() is not None

    def test_retrieve_view_silent_with_backend(self) -> None:
        # Retrieve overrides get_object and never calls filter_queryset, so the
        # guard is skipped even with both filter_set and DjangoFilterBackend.
        class _View(SelectorRetrieveView):
            filter_backends = [DjangoFilterBackend]
            spec = SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_selector, filter_set=_FILTER_SET
            )

        assert _View.as_view() is not None

    def test_viewset_list_action_raises_on_conflict(self) -> None:
        class _ViewSet(SelectorViewSet):
            filter_backends = [DjangoFilterBackend]
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST, selector=_selector, filter_set=_FILTER_SET
                ),
            }

        with pytest.raises(ImproperlyConfigured, match="filter_set"):
            _ViewSet.as_view({"get": "list"})


class TestValidateSuccessStatus:
    def _validate(self, success_status: Any) -> None:
        validate_service_spec(
            ServiceSpec(service=lambda: None, success_status=success_status),
            label="X",
            has_instance=False,
            permissive_extras=False,
        )

    def test_int_passes(self) -> None:
        self._validate(202)

    def test_callable_with_known_params_passes(self) -> None:
        self._validate(lambda *, result, instance: 200)

    def test_callable_with_var_keyword_passes(self) -> None:
        self._validate(lambda **_: 200)

    def test_callable_with_optional_unknown_param_passes(self) -> None:
        # A param with a default is optional from the framework's POV.
        self._validate(lambda *, result, extra=1: 200)

    def test_callable_with_required_unknown_param_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="tenant_id"):
            self._validate(lambda *, tenant_id: 200)

    def test_non_int_non_callable_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be an int"):
            self._validate("200")


class TestValidateResponseFinalizer:
    def _validate(self, finalizer: Any) -> None:
        validate_service_spec(
            ServiceSpec(service=lambda: None, response_finalizer=finalizer),
            label="X",
            has_instance=False,
            permissive_extras=False,
        )

    def test_none_passes(self) -> None:
        self._validate(None)

    def test_callable_with_known_params_passes(self) -> None:
        self._validate(lambda *, response, result: response)

    def test_callable_with_var_keyword_passes(self) -> None:
        self._validate(lambda **_: None)

    def test_callable_with_required_unknown_param_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="tenant"):
            self._validate(lambda *, response, tenant: response)

    def test_non_callable_fails(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a callable"):
            self._validate("nope")


class TestValidatePreconditions:
    def _validate(self, preconditions: Any, **kwargs: Any) -> None:
        validate_service_spec(
            ServiceSpec(service=lambda: None, preconditions=preconditions, **kwargs),
            label="X",
            has_instance=False,
            permissive_extras=False,
        )

    def test_none_passes(self) -> None:
        self._validate(None)

    def test_empty_sequence_passes(self) -> None:
        self._validate([])

    def test_callables_with_known_params_pass(self) -> None:
        self._validate([lambda *, user: None, lambda *, request: None])

    def test_var_keyword_passes(self) -> None:
        self._validate([lambda **_: None])

    def test_bare_callable_is_rejected_with_a_wrapping_hint(self) -> None:
        """The likeliest mistake: forgetting the list."""
        with pytest.raises(ImproperlyConfigured, match=r"Wrap it in a list"):
            self._validate(lambda *, user: None)

    def test_string_is_rejected_rather_than_iterated_per_character(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"Wrap it in a list"):
            self._validate("check_locked")

    def test_non_iterable_is_rejected(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a sequence of callables"):
            self._validate(7)

    def test_non_callable_element_names_its_index(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"preconditions\[1\] is not callable"):
            self._validate([lambda *, user: None, "nope"])

    def test_unseeded_parameter_is_a_config_error_not_a_runtime_typeerror(self) -> None:
        """The plan's sharpest correction: the pool is seed-keyed, not model-keyed."""
        with pytest.raises(ImproperlyConfigured, match="order"):
            self._validate([lambda *, order: None])

    def test_data_parameter_requires_an_input_serializer(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="`data`"):
            self._validate([lambda *, data: None])

    def test_instance_parameter_rejected_on_an_action_without_one(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="`instance`"):
            self._validate([lambda *, instance: None])


class TestNestedPreconditionsRejected:
    """A nested spec never dispatches, so its preconditions never run."""

    def _nested(self) -> SelectorSpec[Any, Any]:
        return SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: None,
            preconditions=[lambda *, user: None],
        )

    def test_instance_selector_spec(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="never invoked"):
            validate_service_spec(
                ServiceSpec(service=lambda: None, instance_selector_spec=self._nested()),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_output_selector_spec(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="never invoked"):
            validate_service_spec(
                ServiceSpec(service=lambda: None, output_selector_spec=self._nested()),
                label="X",
                has_instance=False,
                permissive_extras=False,
            )

    def test_collection_selector_spec(self) -> None:
        nested = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: None,
            preconditions=[lambda *, user: None],
        )
        with pytest.raises(ImproperlyConfigured, match="never invoked"):
            validate_service_spec(
                ServiceSpec(service=lambda: None, collection_selector_spec=nested),
                label="X",
                has_instance=False,
                permissive_extras=False,
            )


class TestNestedPermissionClassesRejected:
    """A nested spec's permissions are never checked, so declaring them fails fast.

    The dispatching spec is the only one whose ``permission_classes`` the view
    resolves, and it does so before anything nested resolves — an inner
    ``IsOwner`` would read as a guard while every caller sailed past it.
    """

    def _nested(self, kind: SelectorKind = SelectorKind.RETRIEVE) -> SelectorSpec[Any, Any]:
        return SelectorSpec(
            kind=kind,
            selector=lambda: None,
            permission_classes=[_AlwaysAllow],
        )

    def test_instance_selector_spec_names_the_field_and_the_position(self) -> None:
        with pytest.raises(
            ImproperlyConfigured,
            match=r"X\.instance_selector_spec: `permission_classes` .* never enforced",
        ):
            validate_service_spec(
                ServiceSpec(service=lambda: None, instance_selector_spec=self._nested()),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_collection_selector_spec_names_the_field_and_the_position(self) -> None:
        with pytest.raises(
            ImproperlyConfigured,
            match=r"X\.collection_selector_spec: `permission_classes` .* never enforced",
        ):
            validate_service_spec(
                ServiceSpec(
                    service=lambda: None,
                    collection_selector_spec=self._nested(SelectorKind.LIST),
                ),
                label="X",
                has_instance=False,
                permissive_extras=False,
            )

    def test_output_selector_spec_names_the_field_and_the_position(self) -> None:
        with pytest.raises(
            ImproperlyConfigured,
            match=r"X\.output_selector_spec: `permission_classes` .* never enforced",
        ):
            validate_service_spec(
                ServiceSpec(service=lambda: None, output_selector_spec=self._nested()),
                label="X",
                has_instance=False,
                permissive_extras=False,
            )

    def test_an_empty_nested_list_is_refused_too(self) -> None:
        # ``[]`` is as unenforced as a populated list; the field simply has no
        # meaning in this position.
        with pytest.raises(ImproperlyConfigured, match="never enforced"):
            validate_service_spec(
                ServiceSpec(
                    service=lambda: None,
                    instance_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        selector=lambda: None,
                        permission_classes=[],
                    ),
                ),
                label="X",
                has_instance=True,
                permissive_extras=False,
            )

    def test_the_dispatching_spec_still_takes_permission_classes(self) -> None:
        validate_service_spec(
            ServiceSpec(
                service=lambda: None,
                permission_classes=[_AlwaysAllow],
                instance_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, selector=lambda: None
                ),
            ),
            label="X",
            has_instance=True,
            permissive_extras=False,
        )


class TestSelectorSpecPreconditions:
    def test_retrieve_may_declare_instance(self) -> None:
        validate_selector_spec(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda: None,
                preconditions=[lambda *, instance: None],
            ),
            label="X",
        )

    def test_list_may_declare_collection(self) -> None:
        validate_selector_spec(
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: None,
                preconditions=[lambda *, collection: None],
            ),
            label="X",
        )

    def test_list_may_not_declare_instance(self) -> None:
        """Pool binding is what stops an instance rule on a LIST spec."""
        with pytest.raises(ImproperlyConfigured, match="`instance`"):
            validate_selector_spec(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: None,
                    preconditions=[lambda *, instance: None],
                ),
                label="X",
            )

    def test_validated_even_when_the_spec_declares_no_selector(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="never invoked|not callable|sequence"):
            validate_selector_spec(
                SelectorSpec(kind=SelectorKind.RETRIEVE, preconditions=["nope"]),
                label="X",
            )


class TestInputSerializerShape:
    """``input_serializer`` is checked for shape, not only for presence.

    ``dataclasses.is_dataclass`` is true of instances too, so the dataclass
    branch downstream accepts a typo that cannot work.
    """

    def _validate(self, input_serializer: Any) -> None:
        validate_service_spec(
            ServiceSpec(service=lambda *, data: None, input_serializer=input_serializer),
            label="X",
            has_instance=False,
            permissive_extras=False,
        )

    def test_a_serializer_subclass_is_accepted(self) -> None:
        self._validate(_InputSerializer)

    def test_a_dataclass_type_is_accepted(self) -> None:
        self._validate(_InputDataclass)

    def test_a_dataclass_instance_is_rejected(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="without the parentheses"):
            self._validate(_InputDataclass(name="x"))

    def test_a_serializer_instance_is_rejected(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a Serializer subclass"):
            self._validate(_InputSerializer())

    def test_a_plain_function_is_rejected(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a Serializer subclass"):
            self._validate(lambda: None)

    def test_none_is_still_no_input(self) -> None:
        validate_service_spec(
            ServiceSpec(service=lambda: None),
            label="X",
            has_instance=False,
            permissive_extras=False,
        )
