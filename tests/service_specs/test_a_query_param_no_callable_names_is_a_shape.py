"""A ``QueryParam`` the spec declares and no callable of the call names compiles to ``shapes``.

Such a parameter shapes what is rendered - a serializer reading ``?fields=``
off the request - rather than what runs, which is the kernel's ``shapes``: a
parameter the caller sends, checked as declared, handed to the presenter alone
on ``PresentationContext.shapes`` and to no callable. One that a callable of
the call does name stays a read, so the callable is still handed it: the
service or selector, a precondition, a target lookup, a ``**kwargs`` taker, a
``kwargs`` or ``input_data`` provider reading it by name or annotated to fill
it, and a ``filter_set`` field. A nested spec's own ``QueryParam`` stays a read,
since the kernel refuses ``shapes`` there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import django_filters
import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny, BasePermission
from typing_extensions import TypedDict, Unpack

from rest_framework_services.service_specs import as_kernel_spec as compiler
from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

_FIELDS = QueryParam("fields", description="The fields to render.", default="title")


class _Title(serializers.Serializer):
    title = serializers.CharField()


class _TitleFilter(django_filters.FilterSet):
    fields = django_filters.CharFilter(field_name="title")

    class Meta:
        model = Post
        fields: list[str] = []


class _Shaping(TypedDict, total=False):
    fields: str


class _Fills(TypedDict):
    fields: str


def _made() -> dict[str, str]:
    return {"title": "made"}


def _every() -> Any:
    return Post.objects.all()


def _by_pk(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _service(**fields: Any) -> ServiceSpec[Any, Any, Any]:
    declared: dict[str, Any] = {
        "service": _made,
        "permission_classes": [AllowAny],
        "reads": [_FIELDS],
    }
    declared.update(fields)
    return ServiceSpec(**declared)


def _selector(**fields: Any) -> SelectorSpec[Any, Any]:
    declared: dict[str, Any] = {
        "kind": SelectorKind.LIST,
        "selector": _every,
        "permission_classes": [],
        "reads": [_FIELDS],
    }
    declared.update(fields)
    return SelectorSpec(**declared)


_SHAPE = Parameter("fields", "string", default="title", help="The fields to render.")


class TestAShape:
    @pytest.mark.parametrize("build", [_service, _selector], ids=["service-spec", "selector-spec"])
    def test_it_compiles_to_shapes_and_not_to_reads(self, build: Callable[[], Any]) -> None:
        compiled = kernel_spec(build())

        assert tuple(compiled.shapes) == (_SHAPE,)
        assert compiled.reads.get("fields") is None
        # Still one of the call's parameters, so the schema and the shape
        # check cover it as any argument.
        assert compiled.parameters().get("fields") == _SHAPE

    def test_a_selector_spec_s_presenter_is_handed_it(self) -> None:
        result = dispatch(_selector(), principal=AnonymousUser(), arguments={"fields": "body"})

        assert result.context is not None
        assert dict(result.context.shapes) == {"fields": "body"}

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param(None, id="the-service-s-own"),
            pytest.param(
                SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda *, result: result,
                    output_serializer=_Title,
                ),
                id="its-output-selector-s",
            ),
        ],
    )
    def test_a_service_spec_s_presenter_is_handed_it(self, output: Any) -> None:
        spec = _service(output_selector_spec=output)

        result = dispatch(spec, principal=AnonymousUser(), arguments={"fields": "body"})

        assert result.context is not None
        assert dict(result.context.shapes) == {"fields": "body"}

    def test_every_variant_of_one_spec_compiles_the_same_shapes(self) -> None:
        spec = _service()

        variant = kernel_spec_variant(
            spec, unguarded=True, server_input=False, spread=True, lenient=True
        )

        assert tuple(variant.shapes) == (_SHAPE,)
        assert tuple(as_kernel_spec(spec, spread=True).shapes) == (_SHAPE,)


_QUERIES: list[dict[str, Any]] = []


class _RecordsTheQuery(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        _QUERIES.append(dict(request.query_params))
        return True


def test_a_permission_class_still_reads_it_off_the_query_string() -> None:
    _QUERIES.clear()
    spec = _selector(permission_classes=[_RecordsTheQuery])

    dispatch(spec, principal=AnonymousUser(), arguments={"fields": "body"})

    assert _QUERIES == [{"fields": ["body"]}]


@pytest.mark.parametrize("declared", [[_FIELDS], []], ids=["a-shape", "none"])
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda reads: _selector(output_serializer=_Title, reads=reads), id="selector"),
        pytest.param(
            lambda reads: _service(
                reads=reads,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=None, output_serializer=_Title
                ),
            ),
            id="service-s-own",
        ),
        pytest.param(
            lambda reads: _service(
                reads=reads,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda *, result: result,
                    output_serializer=_Title,
                ),
            ),
            id="output-selector",
        ),
    ],
)
def test_the_presenter_is_told_whether_the_call_compiles_a_shape(
    monkeypatch: pytest.MonkeyPatch, build: Callable[[Any], Any], declared: list[Any]
) -> None:
    told: list[bool] = []
    presenter = compiler._presenter

    def recording(*args: Any, shaped: bool, **kwargs: Any) -> Any:
        if args[0] is not None:
            told.append(shaped)
        return presenter(*args, shaped=shaped, **kwargs)

    monkeypatch.setattr(compiler, "_presenter", recording)

    as_kernel_spec(build(declared))

    assert told == [bool(declared)]


def _service_naming(*, fields: str = "all") -> dict[str, str]:
    return {"title": fields}


def _service_open(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _service_unpacked(**kwargs: Unpack[_Shaping]) -> dict[str, Any]:
    return dict(kwargs)


def _naming(*, fields: str = "all") -> None: ...


def _selector_naming(*, fields: str = "all") -> Any:
    return Post.objects.all()


def _selector_open(**kwargs: Any) -> Any:
    return Post.objects.all()


def _lookup_naming(*, pk: int = 0, fields: str = "all") -> Any:
    return Post.objects.all()


def _provider_naming(*, fields: str = "all") -> dict[str, Any]:
    return {}


def _provider_open(**kwargs: Any) -> dict[str, Any]:
    return {}


def _provider_filling() -> _Fills:
    return {"fields": "server"}


def _input_naming(*, fields: str = "all") -> dict[str, Any]:
    return {}


def _instance_lookup(selector: Callable[..., Any] = _lookup_naming, **fields: Any) -> Any:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, **fields)


@pytest.mark.parametrize(
    "spec",
    [
        # One case per kind of callable: each fails without its kind counted.
        pytest.param(_service(service=_service_naming), id="service"),
        pytest.param(_service(service=_service_open), id="service-kwargs"),
        pytest.param(_service(service=_service_unpacked), id="service-unpacked-key"),
        pytest.param(_service(preconditions=[_naming]), id="service-precondition"),
        pytest.param(_selector(selector=_selector_naming), id="selector"),
        pytest.param(_selector(selector=_selector_open), id="selector-kwargs"),
        pytest.param(_selector(preconditions=[_naming]), id="selector-precondition"),
        pytest.param(
            _service(service=lambda *, instance: None, instance_selector_spec=_instance_lookup()),
            id="instance-lookup",
        ),
        pytest.param(
            _service(
                service=lambda *, collection: None,
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_lookup_naming
                ),
            ),
            id="collection-lookup",
        ),
        pytest.param(_service(kwargs=_provider_naming), id="service-provider"),
        pytest.param(_service(kwargs=_provider_open), id="service-provider-kwargs"),
        pytest.param(_service(kwargs=_provider_filling), id="service-provider-fills"),
        pytest.param(_selector(kwargs=_provider_naming), id="selector-provider"),
        pytest.param(_selector(kwargs=_provider_filling), id="selector-provider-fills"),
        pytest.param(
            _service(
                service=lambda *, instance: None,
                instance_selector_spec=_instance_lookup(_by_pk, kwargs=_provider_naming),
            ),
            id="lookup-provider",
        ),
        pytest.param(
            _service(input_serializer=_Title, input_data=_input_naming, service=lambda: None),
            id="input-data",
        ),
        pytest.param(_selector(filter_set=_TitleFilter), id="selector-filter"),
    ],
)
def test_a_query_param_a_callable_names_stays_a_read(spec: Any) -> None:
    compiled = kernel_spec(spec)

    assert not compiled.shapes
    assert compiled.parameters().get("fields") is not None


def test_a_nested_spec_s_own_query_param_stays_its_read() -> None:
    spec = ServiceSpec(
        service=lambda *, instance: None,
        permission_classes=[AllowAny],
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup_naming, reads=[QueryParam("fields")]
        ),
    )

    compiled = kernel_spec(spec)

    assert isinstance(compiled, KernelServiceSpec)
    assert not compiled.shapes
    lookup = compiled.instance_selector_spec
    assert isinstance(lookup, KernelSelectorSpec)
    assert not lookup.shapes
    assert lookup.reads.get("fields") == Parameter("fields", "string")


def test_a_url_kwarg_is_never_a_shape() -> None:
    spec = _service(reads=[UrlKwarg("project_pk")])

    compiled = kernel_spec(spec)

    assert not compiled.shapes
    assert compiled.reads.get("project_pk") == Parameter("project_pk", "string")
