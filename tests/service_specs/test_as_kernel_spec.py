"""``as_kernel_spec``: every drfs spec field lands on a kernel seat, or is named as the views'.

The drfs spec is handed to the kernel's own ``kernel_spec``, the entry point
every transport reads it through, so these hold the compile method on the spec
classes as well as the function behind it.
"""

from __future__ import annotations

import datetime
import decimal
import enum
from typing import Annotated, Any, Literal

import django_filters
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.pool.pool_seeds import PoolSeeds
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from typing_extensions import TypedDict, Unpack

from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec
from rest_framework_services.service_specs.drf_permission_check import DRFPermissionCheck
from rest_framework_services.service_specs.serializer_presenter import SerializerPresenter
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Post


class _Note(serializers.Serializer):
    title = serializers.CharField()


class _Owned(serializers.Serializer):
    title = serializers.CharField()
    owner = serializers.HiddenField(default=serializers.CurrentUserDefault())


class _Bounded(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("min_length", 2)
        kwargs.setdefault("max_length", 5)
        super().__init__(*args, **kwargs)


class _Row(serializers.Serializer):
    title = serializers.CharField()

    class Meta:
        list_serializer_class = _Bounded


class _Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class _Extras(TypedDict, total=False):
    project_pk: Annotated[int, InputRequired]
    tenant: Annotated[str, NotClientInput]
    label: str


class _Scale(enum.Enum):
    HALF = 0.5
    ONE_AND_A_HALF = 1.5


class _PostFilter(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields: list[str] = []


def _create(*, data: Any, user: Any) -> Any:
    return data


def _lookup(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _every() -> Any:
    return Post.objects.all()


def _kernel_service(spec: ServiceSpec[Any, Any, Any]) -> KernelServiceSpec:
    compiled = kernel_spec(spec)
    assert isinstance(compiled, KernelServiceSpec)
    return compiled


def _kernel_selector(spec: SelectorSpec[Any, Any]) -> KernelSelectorSpec:
    compiled = kernel_spec(spec)
    assert isinstance(compiled, KernelSelectorSpec)
    return compiled


def _parameter(spec: Any, name: str) -> Parameter:
    found = kernel_spec(spec).parameters().get(name)
    assert found is not None, name
    return found


class TestTheSpecClassesAreKernelSources:
    def test_a_service_spec_compiles_to_the_kernel_s_once(self) -> None:
        spec = ServiceSpec(service=_create, permission_classes=[AllowAny])

        assert isinstance(kernel_spec(spec), KernelServiceSpec)
        assert kernel_spec(spec) is kernel_spec(spec)

    def test_a_selector_spec_compiles_to_the_kernel_s(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_every, permission_classes=[])

        assert isinstance(kernel_spec(spec), KernelSelectorSpec)

    def test_the_function_compiles_afresh(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_every)

        assert as_kernel_spec(spec) is not as_kernel_spec(spec)


class TestDeclaredReads:
    def test_each_url_kwarg_and_query_param_is_a_parameter(self) -> None:
        spec = ServiceSpec(
            service=_create,
            reads=[
                UrlKwarg("project_pk", type="integer", description="The project.", required=True),
                UrlKwarg("team_pk", default="1"),
                QueryParam("fields", description="Fields to return.", default=None),
            ],
        )

        assert tuple(_kernel_service(spec).reads) == (
            Parameter("project_pk", "integer", required=True, help="The project."),
            Parameter("team_pk", "string", default="1"),
            Parameter("fields", "string", default=None, help="Fields to return."),
        )

    def test_a_selector_spec_s_declared_reads_join_its_reflected_ones(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_every, reads=[QueryParam("fields")])

        assert tuple(_kernel_selector(spec).reads) == (Parameter("fields", "string"),)

    def test_a_declared_entry_wins_over_a_reflected_one(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, reads=[UrlKwarg("pk", required=True)]
        )

        assert tuple(_kernel_selector(spec).reads) == (Parameter("pk", "string", required=True),)

    def test_a_declared_name_the_lookup_reads_joins_the_lookup(self) -> None:
        spec = ServiceSpec(
            service=lambda *, instance: instance,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
            reads=[UrlKwarg("pk", type="integer", required=True), UrlKwarg("project_pk")],
        )

        compiled = _kernel_service(spec)
        assert compiled.instance_selector_spec is not None
        assert tuple(compiled.instance_selector_spec.reads) == (
            Parameter("pk", "integer", required=True),
        )
        assert tuple(compiled.reads) == (Parameter("project_pk", "string"),)

    @pytest.mark.parametrize("name", ["result", "queryset", "user", "request", "view"])
    def test_a_name_the_pool_fills_is_refused(self, name: str) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_every, reads=[QueryParam(name)])

        with pytest.raises(ImproperlyConfigured, match=f"SelectorSpec.reads declares '{name}'"):
            as_kernel_spec(spec)

    def test_a_filter_owns_its_name(self) -> None:
        def titled(*, title: int = 0) -> Any:
            return Post.objects.all()

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=titled,
            filter_set=_PostFilter,
            reads=[QueryParam("title")],
        )

        reads = _kernel_selector(spec).reads
        assert [p.name for p in reads] == ["title"]
        # The filter's own Parameter, not the selector's integer or the query param.
        assert reads.get("title") == Parameter("title", "string")


def _hiding_selector(*, tenant: Annotated[str, NotClientInput] = "own") -> Any:
    return Post.objects.all()


def _hiding_service(*, tenant: Annotated[str, NotClientInput] = "own") -> None: ...


def _hiding_precondition(*, tenant: Annotated[str, NotClientInput] = "own") -> None: ...


def _plain_service() -> None: ...


class _HiddenTenant(TypedDict, total=False):
    tenant: Annotated[str, NotClientInput]


def _hiding_unpacked_service(**kwargs: Unpack[_HiddenTenant]) -> None: ...


def _hiding_lookup(*, pk: int, tenant: Annotated[str, NotClientInput] = "own") -> Any:
    return Post.objects.filter(pk=pk)


def _touch_with_tenant(*, instance: Post, tenant: str = "default") -> Post:
    return instance


class TestAReadItsReceiverHides:
    """A declared read is handed by name to the service or selector and to each
    precondition, so one of those marking the same name ``NotClientInput``
    contradicts itself, and the spec is refused at compile."""

    @pytest.mark.parametrize("read", [QueryParam("tenant"), UrlKwarg("tenant")])
    def test_a_selector_hiding_a_read_is_refused(self, read: QueryParam | UrlKwarg) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_hiding_selector, reads=[read])

        with pytest.raises(ImproperlyConfigured) as raised:
            as_kernel_spec(spec)

        message = str(raised.value)
        assert f"SelectorSpec.reads declares {read.__class__.__name__}('tenant')" in message
        assert "_hiding_selector" in message
        assert "NotClientInput" in message

    def test_a_selector_s_precondition_hiding_a_read_is_refused(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_every,
            preconditions=[_hiding_precondition],
            reads=[QueryParam("tenant")],
        )

        with pytest.raises(ImproperlyConfigured, match="_hiding_precondition"):
            as_kernel_spec(spec)

    @pytest.mark.parametrize("read", [QueryParam("tenant"), UrlKwarg("tenant")])
    def test_a_service_hiding_a_read_is_refused(self, read: QueryParam | UrlKwarg) -> None:
        spec = ServiceSpec(service=_hiding_service, reads=[read])

        with pytest.raises(ImproperlyConfigured) as raised:
            as_kernel_spec(spec)

        message = str(raised.value)
        assert f"ServiceSpec.reads declares {read.__class__.__name__}('tenant')" in message
        assert "_hiding_service" in message
        assert "NotClientInput" in message

    def test_a_service_hiding_an_unpacked_key_it_reads_is_refused(self) -> None:
        spec = ServiceSpec(service=_hiding_unpacked_service, reads=[QueryParam("tenant")])

        with pytest.raises(ImproperlyConfigured, match="_hiding_unpacked_service"):
            as_kernel_spec(spec)

    def test_a_service_s_precondition_hiding_a_read_is_refused(self) -> None:
        spec = ServiceSpec(
            service=_plain_service,
            preconditions=[_hiding_precondition],
            reads=[QueryParam("tenant")],
        )

        with pytest.raises(ImproperlyConfigured, match="_hiding_precondition"):
            as_kernel_spec(spec)

    def test_a_read_only_a_target_lookup_hides_is_the_service_s(self) -> None:
        """The lookup is never handed a read it hides, so nothing contradicts itself:
        the read is the service's argument, and the lookup keeps its own key."""
        spec = ServiceSpec(
            service=_touch_with_tenant,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_hiding_lookup
            ),
            reads=[UrlKwarg("tenant")],
        )

        compiled = _kernel_service(spec)

        assert compiled.reads.get("tenant") is not None
        assert compiled.instance_selector_spec is not None
        assert compiled.instance_selector_spec.reads.get("tenant") is None

    def test_a_read_no_receiver_hides_compiles(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_hiding_selector,
            preconditions=[_hiding_precondition],
            reads=[QueryParam("other")],
        )

        assert _kernel_selector(spec).reads.get("other") is not None


class TestReflectedReads:
    def test_a_selector_s_named_parameters_are_its_reads(self) -> None:
        def read(
            *,
            pk: int,
            user: Any,
            request: Any,
            view: Any,
            queryset: Any = None,
            status: Literal["open", "closed"] | None = None,
            colour: _Colour = _Colour.RED,
            when: datetime.datetime | None = None,
            day: datetime.date | None = None,
            amount: decimal.Decimal | None = None,
            ratio: float = 1.0,
            flag: bool = False,
            tags: list[str] | None = None,
            ids: set[int] | None = None,
            bag: list[Any] | None = None,
            extra: dict[str, int] | None = None,
            either: int | str | None = None,
            mixed: Literal[1, "a"] = 1,
            note: Annotated[str, InputDescription("A note.")] = "",
            untyped=None,  # noqa: ANN001
        ) -> Any:
            return None

        reads = _kernel_selector(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=read)).reads

        assert tuple(reads) == (
            Parameter("pk", "integer"),
            Parameter("status", "string", nullable=True, choices=("open", "closed"), default=None),
            Parameter("colour", "string", choices=("red", "blue"), default=_Colour.RED),
            Parameter("when", "string", format="date-time", nullable=True, default=None),
            Parameter("day", "string", format="date", nullable=True, default=None),
            Parameter("amount", "string", format="decimal", nullable=True, default=None),
            Parameter("ratio", "number", default=1.0),
            Parameter("flag", "boolean", default=False),
            Parameter("tags", "array", items="string", nullable=True, default=None),
            Parameter("ids", "array", items="integer", nullable=True, default=None),
            Parameter("bag", "array", nullable=True, default=None),
            Parameter("extra", "object", nullable=True, default=None),
            Parameter("either", "any", nullable=True, default=None),
            Parameter("mixed", "any", default=1),
            Parameter("note", "string", default="", help="A note."),
            Parameter("untyped", "any", default=None),
        )

    def test_choices_take_their_values_json_type(self) -> None:
        def read(
            *,
            size: Literal[1, 2] = 1,
            scale: _Scale = _Scale.HALF,
            on: Literal[True] = True,
            raw: Literal[b"x"] = b"x",
            ids: list[Annotated[int, "a note no schema reads"]] | None = None,
            rows: list = [],  # noqa: B006
            options: dict = {},  # noqa: B006
        ) -> Any:
            return None

        reads = _kernel_selector(SelectorSpec(kind=SelectorKind.LIST, selector=read)).reads

        assert tuple(reads) == (
            Parameter("size", "integer", choices=(1, 2), default=1),
            Parameter("scale", "number", choices=(0.5, 1.5), default=_Scale.HALF),
            Parameter("on", "boolean", choices=(True,), default=True),
            Parameter("raw", "any", default=b"x"),
            Parameter("ids", "array", items="integer", nullable=True, default=None),
            Parameter("rows", "array", default=[]),
            Parameter("options", "object", default={}),
        )

    def test_unpacked_typed_dict_keys_and_markers(self) -> None:
        def read(*args: Any, **extras: Unpack[_Extras]) -> Any:
            return None

        reads = _kernel_selector(SelectorSpec(kind=SelectorKind.LIST, selector=read)).reads

        assert tuple(reads) == (
            Parameter("project_pk", "integer", required=True),
            Parameter("label", "string"),
        )

    def test_a_positional_only_parameter_is_no_read(self) -> None:
        def read(pk: int, /, *, slug: str) -> Any:
            return None

        reads = _kernel_selector(SelectorSpec(kind=SelectorKind.LIST, selector=read)).reads

        assert [p.name for p in reads] == ["slug"]

    def test_a_key_any_callable_hides_is_the_server_s_for_the_whole_call(self) -> None:
        def gate(*, tenant: Annotated[str, NotClientInput]) -> None: ...

        def read(*, tenant: str, pk: int) -> Any:
            return None

        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=read, preconditions=[gate])

        assert [p.name for p in _kernel_selector(spec).reads] == ["pk"]

    def test_a_key_the_service_hides_is_not_a_lookup_read(self) -> None:
        def update(*, instance: Any, pk: Annotated[int, NotClientInput] = 0) -> Any:
            return instance

        spec = ServiceSpec(
            service=update,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        )

        lookup = _kernel_service(spec).instance_selector_spec
        assert lookup is not None
        assert tuple(lookup.reads) == ()

    def test_a_misplaced_marker_is_refused_at_compile(self) -> None:
        def read(*, pk: list[Annotated[int, InputRequired]]) -> Any:
            return None

        with pytest.raises(ImproperlyConfigured):
            as_kernel_spec(SelectorSpec(kind=SelectorKind.LIST, selector=read))

    def test_a_service_without_a_serializer_reads_its_signature_when_spread(self) -> None:
        def archive(*, user: Any, serializer: Any = None, reason: str = "") -> None: ...

        compiled = as_kernel_spec(ServiceSpec(service=archive), spread=True)

        assert tuple(compiled.reads) == (Parameter("reason", "string", default=""),)

    def test_a_service_without_a_serializer_reads_no_signature_by_default(self) -> None:
        # ``AUTO`` and ``BUNDLE`` leave a service's own parameters to its author.
        def archive(*, reason: str = "") -> None: ...

        assert tuple(_kernel_service(ServiceSpec(service=archive)).reads) == ()

    def test_a_serializer_keeps_its_input_under_a_spreading_binding(self) -> None:
        def create(*, data: Any, reason: str = "") -> Any:
            return data

        compiled = as_kernel_spec(ServiceSpec(service=create, input_serializer=_Note), spread=True)

        assert tuple(compiled.reads) == ()

    def test_a_serializer_s_service_reads_no_signature(self) -> None:
        def create(*, data: Any, reason: str = "") -> Any:
            return data

        spec = ServiceSpec(service=create, input_serializer=_Note)

        assert tuple(_kernel_service(spec).reads) == ()

    def test_an_output_selector_reads_nothing(self) -> None:
        def reread(*, result: Any, pk: int = 0) -> Any:
            return result

        spec = ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=reread),
        )

        output = _kernel_service(spec).output_selector_spec
        assert output is not None
        assert tuple(output.reads) == ()

    def test_a_route_capture_nobody_declares_is_no_argument(self) -> None:
        def scoped(*, user: Any) -> Any:
            return Post.objects.all()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=scoped)

        assert "project_pk" not in kernel_spec(spec).parameters().names()


class TestPermissions:
    def test_classes_become_one_drf_check(self) -> None:
        declared = [UrlKwarg("project_pk"), QueryParam("fields")]
        spec = ServiceSpec(service=_create, permission_classes=[AllowAny], reads=declared)

        checks = _kernel_service(spec).permissions
        assert checks is not None
        (check,) = checks

        assert isinstance(check, DRFPermissionCheck)
        assert check.permission_classes == (AllowAny,)
        assert check.url_kwargs == (declared[0],)
        assert check.query_params == (declared[1],)

    def test_an_empty_sequence_is_unrestricted(self) -> None:
        checks = _kernel_service(ServiceSpec(service=_create, permission_classes=[])).permissions
        assert checks is not None
        (check,) = checks

        assert isinstance(check, Unrestricted)

    def test_none_compiles_to_no_permissions_which_a_registry_refuses(self) -> None:
        spec = ServiceSpec(service=_create)

        assert _kernel_service(spec).permissions is None
        with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
            SpecRegistry().register("create", spec)

    def test_a_nested_spec_s_classes_are_not_carried(self) -> None:
        spec = ServiceSpec(
            service=lambda *, instance: instance,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_lookup, permission_classes=[AllowAny]
            ),
        )

        lookup = _kernel_service(spec).instance_selector_spec
        assert lookup is not None
        assert lookup.permissions is None


class TestServiceFields:
    """One test per drfs field: the seat it lands on, or the views' it stays."""

    def test_the_input_serializer_is_the_validator(self) -> None:
        validator = _kernel_service(ServiceSpec(service=_create, input_serializer=_Note)).validator

        assert isinstance(validator, SerializerValidator)
        assert validator.serializer is _Note

    def test_flags_carry_by_name(self) -> None:
        def kwargs() -> dict[str, Any]:
            return {}

        def input_data() -> dict[str, Any]:
            return {}

        def reporter() -> None:
            return None

        def precondition() -> None: ...

        affordance = Affordance(code="open", reason="Open.", when=lambda: True)
        spec = ServiceSpec(
            service=_create,
            atomic=False,
            idempotent=True,
            allow_none=True,
            input_serializer=_Note,
            input_data=input_data,
            kwargs=kwargs,
            progress_reporter=reporter,
            preconditions=[precondition],
            affordances=[affordance],
            metadata={"team": "core"},
        )

        compiled = _kernel_service(spec)

        assert (compiled.atomic, compiled.idempotent, compiled.allow_none) == (False, True, True)
        assert compiled.kwargs is kwargs
        assert compiled.input_data is input_data
        assert compiled.progress_reporter is reporter
        assert compiled.preconditions == (precondition,)
        assert compiled.affordances == (affordance,)
        assert compiled.metadata == {"team": "core"}

    def test_many_carries_its_argument_and_the_list_serializer_s_bounds(self) -> None:
        spec = ServiceSpec(service=_create, input_serializer=_Row, many=True, many_argument="rows")

        compiled = _kernel_service(spec)

        assert (compiled.many, compiled.many_argument) == (True, "rows")
        assert (compiled.many_min_length, compiled.many_max_length) == (2, 5)

    def test_a_list_that_may_not_be_empty_has_a_minimum_of_one(self) -> None:
        class NotEmpty(serializers.Serializer):
            title = serializers.CharField()

            @classmethod
            def many_init(cls, *args: Any, **kwargs: Any) -> Any:
                kwargs["allow_empty"] = False
                return super().many_init(*args, **kwargs)

        spec = ServiceSpec(service=_create, input_serializer=NotEmpty, many=True)

        compiled = _kernel_service(spec)

        assert (compiled.many_min_length, compiled.many_max_length) == (1, None)

    def test_a_bounded_serializer_without_many_has_no_list_bounds(self) -> None:
        compiled = _kernel_service(ServiceSpec(service=_create, input_serializer=_Row))

        assert (compiled.many_min_length, compiled.many_max_length) == (None, None)

    def test_many_with_no_serializer_is_the_kernel_s_refusal(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="many=True with no validator"):
            as_kernel_spec(ServiceSpec(service=_create, many=True))

    def test_a_dataclass_list_is_unbounded(self) -> None:
        @__import__("dataclasses").dataclass
        class Row:
            title: str

        compiled = _kernel_service(ServiceSpec(service=_create, input_serializer=Row, many=True))

        assert (compiled.many_min_length, compiled.many_max_length) == (None, None)

    @pytest.mark.parametrize(
        ("declared", "override", "expected"),
        [(None, None, False), (False, None, False), (True, None, True), (None, True, True)],
    )
    def test_partial(self, declared: bool | None, override: bool | None, expected: bool) -> None:
        spec = ServiceSpec(service=_create, input_serializer=_Note, partial=declared)

        compiled = as_kernel_spec(spec, partial=override)

        assert isinstance(compiled, KernelServiceSpec)
        assert compiled.partial is expected

    def test_partial_with_no_serializer_relaxes_nothing_and_is_dropped(self) -> None:
        assert _kernel_service(ServiceSpec(service=_create, partial=True)).partial is False

    def test_the_views_fields_are_not_carried(self) -> None:
        """``success_status``, ``document_service_error``, ``response_finalizer``: the HTTP views'."""
        spec = ServiceSpec(
            service=_create,
            success_status=201,
            document_service_error=True,
            response_finalizer=lambda: None,
        )

        compiled = _kernel_service(spec)

        assert compiled == _kernel_service(ServiceSpec(service=_create))

    def test_the_serializer_context_callables_ride_on_the_adapters(self) -> None:
        """``input_serializer_context`` on the Validator, ``output_serializer_context`` on the Presenter."""

        def input_context() -> dict[str, Any]:
            return {"a": 1}

        def output_context() -> dict[str, Any]:
            return {"b": 2}

        spec = ServiceSpec(
            service=_create,
            input_serializer=_Note,
            input_serializer_context=input_context,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=_Note,
                output_serializer_context=output_context,
            ),
        )

        compiled = _kernel_service(spec)

        assert isinstance(compiled.validator, SerializerValidator)
        assert compiled.validator.serializer_context is input_context
        assert isinstance(compiled.presenter, SerializerPresenter)
        assert compiled.presenter.serializer_context is output_context
        assert compiled.presenter.value_keyword == "result"

    def test_an_output_selector_s_presenter_reads_the_result_and_a_retrieve_s_the_instance(
        self,
    ) -> None:
        def output_context() -> dict[str, Any]:
            return {}

        selected = _kernel_service(
            ServiceSpec(
                service=_create,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_lookup,
                    output_serializer=_Note,
                    output_serializer_context=output_context,
                ),
            )
        )
        retrieve = _kernel_selector(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_lookup,
                output_serializer=_Note,
                output_serializer_context=output_context,
            )
        )

        assert selected.output_selector_spec is not None
        output_presenter = selected.output_selector_spec.presenter
        assert isinstance(output_presenter, SerializerPresenter)
        assert output_presenter.serializer_context is output_context
        assert output_presenter.value_keyword == "result"
        assert isinstance(retrieve.presenter, SerializerPresenter)
        assert retrieve.presenter.value_keyword == "instance"


class TestTheOutput:
    def test_a_selector_less_output_moves_its_serializer_onto_the_service(self) -> None:
        spec = ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Note),
        )

        compiled = _kernel_service(spec)

        assert compiled.output_selector_spec is None
        assert isinstance(compiled.presenter, SerializerPresenter)
        assert compiled.presenter.serializer_class is _Note

    def test_a_selector_less_output_with_no_serializer_presents_the_return(self) -> None:
        spec = ServiceSpec(
            service=_create, output_selector_spec=SelectorSpec(kind=SelectorKind.LIST)
        )

        compiled = _kernel_service(spec)

        assert (compiled.output_selector_spec, compiled.presenter) == (None, None)

    def test_an_output_selector_keeps_its_serializer_and_loses_its_kwargs(self) -> None:
        def reread(*, result: Any) -> Any:
            return result

        def kwargs() -> dict[str, Any]:
            return {}

        spec = ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=reread, output_serializer=_Note, kwargs=kwargs
            ),
        )

        output = _kernel_service(spec).output_selector_spec

        assert output is not None
        assert output.kwargs is None
        assert isinstance(output.presenter, SerializerPresenter)

    def test_field_audiences_layer_over_the_output_serializer(self) -> None:
        hidden = FieldMarking.hidden()
        spec = ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Note),
            field_audiences={"title": hidden},
        )

        output = kernel_spec(spec).output()

        assert output is not None
        field = output.get("title")
        assert field is not None
        assert field.marking == hidden

    def test_the_service_spec_s_audiences_win_over_its_output_selector_s(self) -> None:
        def reread(*, result: Any) -> Any:
            return result

        label = FieldMarking.label("Name")
        spec = ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=reread,
                output_serializer=_Note,
                field_audiences={"title": FieldMarking.hidden()},
            ),
            field_audiences={"title": label},
        )

        output = kernel_spec(spec).output()

        assert output is not None
        field = output.get("title")
        assert field is not None
        assert field.marking == label

    def test_a_selector_spec_s_audiences_layer_over_its_serializer(self) -> None:
        hidden = FieldMarking.hidden()
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_every,
            output_serializer=_Note,
            field_audiences={"title": hidden},
        )

        output = kernel_spec(spec).output()

        assert output is not None
        field = output.get("title")
        assert field is not None
        assert field.marking == hidden

    @pytest.mark.parametrize(
        "spec",
        [
            pytest.param(
                lambda: SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_every,
                    field_audiences={"title": FieldMarking.hidden()},
                ),
                id="selector",
            ),
            pytest.param(
                lambda: ServiceSpec(
                    service=_create, field_audiences={"title": FieldMarking.hidden()}
                ),
                id="service-without-output",
            ),
            pytest.param(
                lambda: ServiceSpec(
                    service=_create,
                    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE),
                    field_audiences={"title": FieldMarking.hidden()},
                ),
                id="service-output-without-serializer",
            ),
        ],
    )
    def test_audiences_with_no_output_serializer_are_refused(self, spec: Any) -> None:
        with pytest.raises(
            ImproperlyConfigured, match="declares field_audiences and no output serializer"
        ):
            as_kernel_spec(spec())


class TestSelectorFields:
    def test_the_shaping_fields_carry(self) -> None:
        def kwargs() -> dict[str, Any]:
            return {}

        def precondition() -> None: ...

        def reporter() -> None:
            return None

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_every,
            allow_none=True,
            select_related=["author"],
            prefetch_related=["tags"],
            annotations={"n": 1},
            filter_set=_PostFilter,
            kwargs=kwargs,
            preconditions=[precondition],
            progress_reporter=reporter,
            metadata={"a": 1},
        )

        compiled = _kernel_selector(spec)

        assert compiled.kind is SelectorKind.LIST
        assert compiled.allow_none is True
        assert compiled.select_related == ("author",)
        assert compiled.prefetch_related == ("tags",)
        assert compiled.annotations == {"n": 1}
        assert compiled.filter_set is _PostFilter
        assert compiled.kwargs is kwargs
        assert compiled.preconditions == (precondition,)
        assert compiled.progress_reporter is reporter
        assert compiled.metadata == {"a": 1}

    def test_affordances_map_to_each_service_s_compiled_spec(self) -> None:
        archive = ServiceSpec(
            service=_create,
            affordances=[Affordance(code="open", reason="Open.", when=lambda: True)],
        )
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_every, affordances={"archive": archive}
        )

        compiled = _kernel_selector(spec)

        assert compiled.affordances == {"archive": kernel_spec(archive)}

    @pytest.mark.django_db
    def test_extend_queryset_is_called_positionally(self) -> None:
        Post.objects.create(title="kept")
        Post.objects.create(title="dropped")
        calls: list[tuple[Any, Any]] = []

        def extend(queryset: Any, view: Any, request: Any) -> Any:
            calls.append((view, request))
            return queryset.filter(title="kept")

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_every,
            extend_queryset=extend,
            permission_classes=[],
        )

        result = dispatch(
            spec,
            principal=AnonymousUser(),
            arguments={},
            pool_seeds=PoolSeeds(
                seeds=(("view", lambda: "the view"), ("request", lambda: "the request"))
            ),
        )

        assert [post.title for post in result.value] == ["kept"]
        assert calls == [("the view", "the request")]

    def test_an_instance_lookup_s_allow_none_is_stripped(self) -> None:
        spec = ServiceSpec(
            service=lambda *, instance: instance,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_lookup,
                allow_none=True,
                output_serializer=_Note,
            ),
        )

        lookup = _kernel_service(spec).instance_selector_spec

        assert lookup is not None
        assert lookup.allow_none is False
        assert lookup.presenter is None

    def test_a_collection_lookup_keeps_its_allow_none(self) -> None:
        spec = ServiceSpec(
            service=lambda *, collection: None,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_every, allow_none=True
            ),
        )

        lookup = _kernel_service(spec).collection_selector_spec

        assert lookup is not None
        assert lookup.allow_none is True


class TestRefusals:
    @pytest.mark.parametrize("role", ["instance", "output"])
    @pytest.mark.parametrize(
        ("field", "value"),
        [("preconditions", [lambda: None]), ("progress_reporter", lambda: None)],
    )
    def test_a_nested_run_that_would_never_run_is_refused(
        self, role: str, field: str, value: Any
    ) -> None:
        nested = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_lookup if role == "instance" else None,
            **{field: value},
        )
        spec = (
            ServiceSpec(service=lambda *, instance: None, instance_selector_spec=nested)
            if role == "instance"
            else ServiceSpec(service=_create, output_selector_spec=nested)
        )

        with pytest.raises(
            ImproperlyConfigured,
            match=f"ServiceSpec.{role}_selector_spec declares {field}",
        ):
            as_kernel_spec(spec)

    @pytest.mark.parametrize("role", [None, "collection"])
    def test_a_selector_of_none_outside_an_output_is_refused(self, role: str | None) -> None:
        nested = SelectorSpec(kind=SelectorKind.RETRIEVE if role is None else SelectorKind.LIST)
        spec: Any = (
            nested
            if role is None
            else ServiceSpec(service=lambda *, collection: None, collection_selector_spec=nested)
        )

        with pytest.raises(ImproperlyConfigured, match="declares no selector"):
            as_kernel_spec(spec)

    def test_an_instance_lookup_with_no_selector_compiles_to_no_lookup(self) -> None:
        """drfs reads it as none: the view's ``get_object()`` supplies the row."""
        spec = ServiceSpec(
            service=lambda *, instance: None,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE),
        )

        compiled: Any = as_kernel_spec(spec)

        assert compiled.instance_selector_spec is None


@pytest.mark.django_db
def test_a_current_user_default_reads_the_principal() -> None:
    principal = get_user_model().objects.create(username="ada")
    spec = ServiceSpec(service=_create, input_serializer=_Owned, permission_classes=[])

    result = dispatch(spec, principal=principal, arguments={"title": "Hi"})

    assert result.value == {"title": "Hi", "owner": principal}


@pytest.mark.parametrize(
    ("output", "allow_none"),
    [
        (None, True),
        (SelectorSpec(kind=SelectorKind.RETRIEVE), True),
        (SelectorSpec(kind=SelectorKind.LIST, selector=_every), True),
        (SelectorSpec(kind=SelectorKind.LIST), False),
    ],
    ids=["no-output", "retrieve-no-re-read", "list-re-read", "list-no-re-read"],
)
def test_only_a_list_output_with_no_re_read_turns_allow_none_off(
    output: Any, allow_none: bool
) -> None:
    """Each case holds one conjunct of ``_lists_its_return``: the first its
    ``None`` check, the second the kind, the third the selector."""
    spec = ServiceSpec(service=_create, output_selector_spec=output, allow_none=True)

    assert _kernel_service(spec).allow_none is allow_none
