"""A reflected parameter with no default, passable by keyword, compiles as required.

Nothing but an argument can fill it, and the callable raises ``TypeError``
without one, so the kernel's Parameter and its input schema say ``required``,
as drfs' own ``spec_to_json_schema`` says under ``supplied=``. Each way the
server fills one named here still answers, on drfs' edge and on the kernel's
own ``dispatch``: a route capture, a view hook's ``extra_kwargs``, the spec's
``kwargs`` provider, ``input_data`` beside it, a registered seed and a target
lookup's own provider.

A row handed in is the exception. drfs' edge dispatches each call through a
variant that relaxes exactly the reads this rule requires
(``required_for_having_no_default``), so a view handing the row it resolved
answers without the lookup's key, as it always did, and a missing argument is
refused at the stage the edge documents. The kernel's own route checks the
arguments before it reads ``target=``, so a caller handing one there still
sends the key.

Three exceptions keep a parameter optional:

- a positional-only one is no read at all, since dispatch binds by keyword;
- a spec, or its target lookup, whose ``kwargs`` provider says nothing about
  its keys may fill any name, so none is required of the caller (a typed one
  says which, and the kernel relaxes exactly those);
- a wrapper taking ``**kwargs`` may fill any of its parameters itself.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Annotated, Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.schema.spec_input_schema import spec_input_schema
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ArgumentBinding,
    ViewHooks,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.service_specs.as_kernel_spec import (
    as_kernel_spec,
    required_for_having_no_default,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("edge", [_sync, _async], ids=["sync", "async"])


class _Status(TypedDict):
    status: str


class _Pk(TypedDict):
    pk: int


class _TitleData(TypedDict):
    title: str


class _Title(serializers.Serializer):
    title = serializers.CharField()


class _TitleFilter(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields: list[str] = []


def _by_status(*, status: str, page: int = 1) -> dict[str, Any]:
    return {"status": status, "page": page}


def _by_pk(pk: int) -> dict[str, Any]:
    return {"pk": pk}


def _by_tenant(*, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


def _lookup(*, pk: int) -> Any:
    return Post.objects.get(pk=pk)


def _lookup_rows(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _untouched(*, instance: Any) -> Any:
    return instance.title


def _spread(*, note: str, tone: str = "plain") -> dict[str, Any]:
    return {"note": note, "tone": tone}


def _open(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _wants_reason(*, reason: str) -> None: ...


def _wants_status(*, status: str) -> None: ...


def _open_selector(*, page: int = 1) -> dict[str, Any]:
    return {"page": page}


def _status_provider() -> _Status:
    return {"status": "provided"}


def _server_title() -> _TitleData:
    return {"title": "server"}


def _pk_provider() -> _Pk:
    return {"pk": Post.objects.get(title="provided").pk}


def _filling(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> Any:
        kwargs.setdefault("status", "wrapped")
        return fn(**kwargs)

    return wrapper


def _selector(selector: Callable[..., Any], **fields: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=selector, permission_classes=[], **fields
    )


def _read(compiled: Any, name: str) -> Parameter:
    found = compiled.parameters().get(name)
    assert found is not None, name
    return found


def _target_reads(compiled: Any) -> Any:
    assert isinstance(compiled, KernelServiceSpec)
    target = compiled.target_selector_spec()
    assert isinstance(target, KernelSelectorSpec)
    return target.reads


class TestTheDeclaration:
    @pytest.mark.parametrize(
        ("compile_it", "name"),
        [
            pytest.param(
                lambda: kernel_spec(_selector(_by_status)).parameters(), "status", id="keyword-only"
            ),
            pytest.param(
                lambda: kernel_spec(_selector(_by_pk)).parameters(),
                "pk",
                id="positional-or-keyword",
            ),
            pytest.param(
                lambda: _target_reads(
                    kernel_spec(
                        ServiceSpec(
                            service=_untouched,
                            permission_classes=[AllowAny],
                            instance_selector_spec=SelectorSpec(
                                kind=SelectorKind.RETRIEVE, selector=_lookup
                            ),
                        )
                    )
                ),
                "pk",
                id="target-lookup",
            ),
            pytest.param(
                lambda: as_kernel_spec(
                    ServiceSpec(service=_spread, permission_classes=[AllowAny]), spread=True
                ).parameters(),
                "note",
                id="spread-service",
            ),
            pytest.param(
                lambda: as_kernel_spec(
                    ServiceSpec(
                        service=_open,
                        preconditions=[_wants_reason],
                        permission_classes=[AllowAny],
                    ),
                    spread=True,
                ).parameters(),
                "reason",
                id="spread-precondition",
            ),
        ],
    )
    def test_a_parameter_with_no_default_is_required(
        self, compile_it: Callable[[], Any], name: str
    ) -> None:
        assert compile_it().get(name).required is True

    def test_a_parameter_with_a_default_stays_optional(self) -> None:
        assert _read(kernel_spec(_selector(_by_status)), "page").required is False

    def test_the_kernel_s_input_schema_lists_it(self) -> None:
        schema = spec_input_schema(kernel_spec(_selector(_by_status)))

        assert schema["required"] == ["status"]

    def test_a_positional_only_parameter_is_no_read_and_so_never_required(self) -> None:
        def positional(pk: int, /) -> Any:
            return pk

        assert kernel_spec(_selector(positional)).parameters().get("pk") is None


class TestAnUntypedProviderKeepsEveryParameterOptional:
    """It may fill any name, so the caller is asked for none, as drfs' own schema asks."""

    def test_a_selector_spec_s_own_provider(self) -> None:
        spec = _selector(_by_status, kwargs=lambda: {"status": "provided"})

        assert _read(kernel_spec(spec), "status").required is False
        result = dispatch(spec, principal=AnonymousUser(), arguments={})
        assert result.value == {"status": "provided", "page": 1}

    def test_a_service_spec_s_provider_keeps_its_target_lookup_s_parameter_optional(
        self,
    ) -> None:
        post = Post.objects.create(title="kept")
        spec = ServiceSpec(
            service=_untouched,
            permission_classes=[AllowAny],
            kwargs=lambda: {"pk": post.pk},
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        )

        assert _target_reads(kernel_spec(spec)).get("pk").required is False

    def test_a_target_lookup_s_own_provider(self) -> None:
        post = Post.objects.create(title="kept")
        spec = ServiceSpec(
            service=_untouched,
            permission_classes=[AllowAny],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_lookup, kwargs=lambda: {"pk": post.pk}
            ),
        )

        assert _target_reads(kernel_spec(spec)).get("pk").required is False
        result = dispatch(spec, principal=AnonymousUser(), arguments={})
        assert result.value == "kept"

    @pytest.mark.parametrize(
        ("service", "preconditions", "name"),
        [
            pytest.param(_spread, [], "note", id="spread-service"),
            # A bare ``**kwargs`` service takes its preconditions' parameters.
            pytest.param(_open, [_wants_reason], "reason", id="spread-precondition"),
        ],
    )
    def test_a_spread_service_s_parameter(
        self, service: Callable[..., Any], preconditions: list[Any], name: str
    ) -> None:
        spec = ServiceSpec(
            service=service,
            permission_classes=[AllowAny],
            preconditions=preconditions,
            kwargs=lambda: {},
        )

        assert _read(as_kernel_spec(spec, spread=True), name).required is False

    def test_a_typed_provider_keeps_the_parameter_required(self) -> None:
        spec = _selector(_by_status, kwargs=_status_provider)

        assert _read(kernel_spec(spec), "status").required is True


class TestAWrapperFillingItsOwnParameter:
    def test_its_parameter_stays_optional_and_the_wrapper_fills_it(self) -> None:
        spec = _selector(_filling(_by_status))

        assert _read(kernel_spec(spec), "status").required is False
        result = dispatch(spec, principal=AnonymousUser(), arguments={})
        assert result.value == {"status": "wrapped", "page": 1}


class TestEachServerFillerAnswers:
    """Each holds the parameter required and the server's value reaching it.

    A row handed in is no such filler on the kernel's route (``TestARowHandedIn``).
    """

    @_CORES
    def test_a_route_capture(self, edge: Any) -> None:
        spec = _selector(_by_pk)
        context = build_offline_context(None, kwargs={"pk": 7})

        assert _read(kernel_spec(spec), "pk").required is True
        result = edge(spec, params={}, request=context.request, view=context.view)
        assert result.value == {"pk": 7}
        # A kernel transport carries the capture as the argument it is.
        assert dispatch(spec, principal=AnonymousUser(), arguments={"pk": 7}).value == {"pk": 7}

    @_CORES
    def test_a_view_hook_s_extra_kwargs(self, edge: Any) -> None:
        spec = _selector(_by_status)

        assert _read(kernel_spec(spec), "status").required is True
        result = edge(spec, params={}, view_hooks=ViewHooks(extra_kwargs={"status": "hooked"}))
        assert result.value == {"status": "hooked", "page": 1}

    @_CORES
    def test_the_spec_s_typed_provider(self, edge: Any) -> None:
        spec = _selector(_by_status, kwargs=_status_provider)

        assert _read(kernel_spec(spec), "status").required is True
        assert edge(spec, params={}).value == {"status": "provided", "page": 1}
        result = dispatch(spec, principal=AnonymousUser(), arguments={})
        assert result.value == {"status": "provided", "page": 1}

    @_CORES
    def test_the_spec_s_untyped_provider(self, edge: Any) -> None:
        provided = _selector(_by_status, kwargs=lambda: {"status": "provided"})

        # Required without the provider, so the provider is what relaxes it.
        assert _read(kernel_spec(_selector(_by_status)), "status").required is True
        assert _read(kernel_spec(provided), "status").required is False
        assert edge(provided, params={}).value == {"status": "provided", "page": 1}
        result = dispatch(provided, principal=AnonymousUser(), arguments={})
        assert result.value == {"status": "provided", "page": 1}

    @_CORES
    def test_input_data_beside_a_required_lookup_parameter(self, edge: Any) -> None:
        post = Post.objects.create(title="kept")
        spec = ServiceSpec(
            service=lambda *, data, instance: (data["title"], instance.pk),
            permission_classes=[AllowAny],
            input_serializer=_Title,
            # Typed, so the kernel's route relaxes the field it fills too.
            input_data=_server_title,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        )

        assert _target_reads(kernel_spec(spec)).get("pk").required is True
        assert edge(spec, params={"pk": post.pk}).value == ("server", post.pk)
        result = dispatch(spec, principal=AnonymousUser(), arguments={"pk": post.pk})
        assert result.value == ("server", post.pk)

    @_CORES
    def test_a_registered_seed(self, edge: Any) -> None:
        spec = _selector(_by_tenant)
        seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")

        # Unseeded, nothing but the caller fills it.
        assert _read(kernel_spec(spec), "tenant").required is True
        assert edge(spec, params={}, pool_seeds=seeds).value == {"tenant": "acme"}
        seeded = as_kernel_spec(spec, seeded=frozenset({"tenant"}))
        assert seeded.parameters().get("tenant") is None
        result = dispatch(seeded, principal=AnonymousUser(), arguments={}, pool_seeds=seeds)
        assert result.value == {"tenant": "acme"}

    @pytest.mark.parametrize("filtered", [False, True], ids=["plain", "beside-a-filter"])
    @_CORES
    def test_a_target_lookup_s_typed_provider(self, edge: Any, filtered: bool) -> None:
        Post.objects.create(title="provided")
        spec = ServiceSpec(
            service=lambda *, collection: [post.title for post in collection],
            permission_classes=[AllowAny],
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_lookup_rows,
                kwargs=_pk_provider,
                filter_set=_TitleFilter if filtered else None,
            ),
        )

        assert _target_reads(kernel_spec(spec)).get("pk").required is True
        assert edge(spec, params={}).value == ["provided"]
        assert dispatch(spec, principal=AnonymousUser(), arguments={}).value == ["provided"]


def test_a_bundled_selector_s_parameter_stays_the_author_s_to_fill() -> None:
    # The edge relaxes a bundled selector's reflected reads itself, so a value
    # the server gives still reaches it under ``BUNDLE``.
    context = build_offline_context(None, kwargs={"pk": 3})
    result = dispatch_spec(
        _selector(_by_pk),
        user=None,
        params={},
        request=context.request,
        view=context.view,
        argument_binding=ArgumentBinding.BUNDLE,
    )

    assert result.value == {"pk": 3}


class TestARowHandedIn:
    """The edge answers it without the lookup's key; the kernel's route still asks for the key."""

    def _spec(self) -> ServiceSpec[Any, Any, Any]:
        return ServiceSpec(
            service=_untouched,
            permission_classes=[AllowAny],
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        )

    @_CORES
    def test_the_edge_answers_it_without_the_key(self, edge: Any) -> None:
        post = Post.objects.create(title="handed")

        assert edge(self._spec(), params={}, instance=post).value == "handed"

    @_CORES
    @pytest.mark.parametrize("declare", [QueryParam, UrlKwarg], ids=["query-param", "url-kwarg"])
    @pytest.mark.parametrize("on", ["spec", "lookup"])
    def test_the_edge_answers_it_without_a_key_the_spec_declares(
        self, edge: Any, declare: Any, on: str
    ) -> None:
        """A declared read of the lookup's key says nothing of requiredness, so the edge relaxes it too.

        Held per seat of the read: the spec's own ``reads`` and the lookup's
        (``_saying_required`` at each of its sites).
        """
        post = Post.objects.create(title="handed")
        spec = ServiceSpec(
            service=_untouched,
            permission_classes=[AllowAny],
            reads=[declare("pk")] if on == "spec" else [],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_lookup,
                reads=[declare("pk")] if on == "lookup" else [],
            ),
        )

        assert edge(spec, params={}, instance=post).value == "handed"

    def test_the_kernel_s_route_still_asks_for_the_key(self) -> None:
        post = Post.objects.create(title="handed")

        with pytest.raises(KernelInvalidArguments) as refused:
            dispatch(self._spec(), principal=AnonymousUser(), arguments={}, target=post)
        assert set(refused.value.detail) == {"pk"}


class TestTheEdgeRefusesAMissingReadAsBefore:
    """A call leaving out a declared read the callable needs is refused as it was, now the read is required."""

    @_CORES
    @pytest.mark.parametrize(
        ("spec", "name"),
        [
            pytest.param(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_open_selector,
                    permission_classes=[AllowAny],
                    reads=[QueryParam("status")],
                    preconditions=[_wants_status],
                ),
                "status",
                id="selector-precondition",
            ),
            pytest.param(
                ServiceSpec(
                    service=_spread, permission_classes=[AllowAny], reads=[QueryParam("note")]
                ),
                "note",
                id="auto-service",
            ),
            pytest.param(
                ServiceSpec(
                    service=_open,
                    permission_classes=[AllowAny],
                    reads=[QueryParam("reason")],
                    preconditions=[_wants_reason],
                ),
                "reason",
                id="auto-service-precondition",
            ),
        ],
    )
    def test_the_key_is_asked_for_in_the_drfs_words(self, edge: Any, spec: Any, name: str) -> None:
        with pytest.raises(InvalidArguments) as refused:
            edge(spec, params={})

        assert set(refused.value.detail) == {name}


class _Kwargs(TypedDict):
    # A required key named like the ``**kwargs`` it is unpacked into.
    kwargs: int


def _keyed(**kwargs: Unpack[_Kwargs]) -> Any:
    return None


def _marked(*, pk: Annotated[int, InputRequired()]) -> Any:
    return None


def _open_selector_taking_pk(*, pk: Annotated[int, InputRequired()]) -> Any:
    return None


def _wants_pk(*, pk: int) -> None: ...


def _defaulted(*, pk: int = 0, status: str) -> Any:
    return None


def _collection(*, pk: int) -> Any:
    return Post.objects.filter(pk=pk)


def _lookup_service(role: str, selector: Callable[..., Any], **fields: Any) -> Any:
    kind = SelectorKind.RETRIEVE if role == "instance" else SelectorKind.LIST
    reads = fields.pop("lookup_reads", ())
    return ServiceSpec(
        service=lambda **kwargs: None,
        permission_classes=[AllowAny],
        **{f"{role}_selector_spec": SelectorSpec(kind=kind, selector=selector, reads=reads)},
        **fields,
    )


def _spread_service(service: Callable[..., Any], **fields: Any) -> Any:
    return ServiceSpec(service=service, permission_classes=[AllowAny], **fields)


def _own(spec: Any, **options: Any) -> Any:
    return kernel_spec_variant(spec, unguarded=True, server_input=False, **options).reads


def _lookup_s(spec: Any, **options: Any) -> Any:
    return _target_reads(kernel_spec_variant(spec, unguarded=True, server_input=False, **options))


@pytest.mark.parametrize(
    ("compiled", "variant", "name", "relaxed"),
    [
        # Relaxed: what the rule alone requires, on each seat it reaches.
        pytest.param(
            lambda: kernel_spec(_selector(_by_status)).reads,
            lambda: _own(_selector(_by_status)),
            "status",
            True,
            id="selector",
        ),
        pytest.param(
            lambda: _target_reads(kernel_spec(_lookup_service("instance", _lookup))),
            lambda: _lookup_s(_lookup_service("instance", _lookup)),
            "pk",
            True,
            id="instance-lookup",
        ),
        pytest.param(
            lambda: _target_reads(kernel_spec(_lookup_service("collection", _collection))),
            lambda: _lookup_s(_lookup_service("collection", _collection)),
            "pk",
            True,
            id="collection-lookup",
        ),
        pytest.param(
            lambda: as_kernel_spec(_spread_service(_spread), spread=True).reads,
            lambda: _own(_spread_service(_spread), spread=True),
            "note",
            True,
            id="spread-service",
        ),
        pytest.param(
            lambda: (
                as_kernel_spec(
                    _spread_service(_open, preconditions=[_wants_reason]), spread=True
                ).reads
            ),
            lambda: _own(_spread_service(_open, preconditions=[_wants_reason]), spread=True),
            "reason",
            True,
            id="spread-precondition",
        ),
        # A declared read of the name that says nothing of requiredness: the
        # parameter has no default, so the rule alone requires it, and the edge
        # relaxes it as it does with no read declared.
        pytest.param(
            lambda: kernel_spec(_selector(_by_status, reads=[UrlKwarg("status")])).reads,
            lambda: _own(_selector(_by_status, reads=[UrlKwarg("status")])),
            "status",
            True,
            id="selector-declared-without-required",
        ),
        pytest.param(
            lambda: (
                as_kernel_spec(
                    _spread_service(_spread, reads=[QueryParam("note")]), spread=True
                ).reads
            ),
            lambda: _own(_spread_service(_spread, reads=[QueryParam("note")]), spread=True),
            "note",
            True,
            id="spread-service-declared-without-required",
        ),
        pytest.param(
            lambda: _target_reads(
                kernel_spec(_lookup_service("instance", _lookup, reads=[QueryParam("pk")]))
            ),
            lambda: _lookup_s(_lookup_service("instance", _lookup, reads=[QueryParam("pk")])),
            "pk",
            True,
            id="lookup-declared-by-the-spec-without-required",
        ),
        pytest.param(
            lambda: _target_reads(
                kernel_spec(_lookup_service("collection", _lookup, lookup_reads=[QueryParam("pk")]))
            ),
            lambda: _lookup_s(
                _lookup_service("collection", _lookup, lookup_reads=[QueryParam("pk")])
            ),
            "pk",
            True,
            id="lookup-declared-by-the-lookup-without-required",
        ),
        pytest.param(
            lambda: (
                kernel_spec(
                    _selector(
                        _open_selector, reads=[QueryParam("status")], preconditions=[_wants_status]
                    )
                ).reads
            ),
            lambda: _own(
                _selector(
                    _open_selector, reads=[QueryParam("status")], preconditions=[_wants_status]
                )
            ),
            "status",
            True,
            id="selector-precondition-declared-without-required",
        ),
        pytest.param(
            lambda: kernel_spec(_spread_service(_spread, reads=[QueryParam("note")])).reads,
            lambda: _own(_spread_service(_spread, reads=[QueryParam("note")])),
            "note",
            True,
            id="auto-service-declared-without-required",
        ),
        pytest.param(
            lambda: (
                kernel_spec(
                    _spread_service(
                        _open, reads=[QueryParam("reason")], preconditions=[_wants_reason]
                    )
                ).reads
            ),
            lambda: _own(
                _spread_service(_open, reads=[QueryParam("reason")], preconditions=[_wants_reason])
            ),
            "reason",
            True,
            id="auto-service-precondition-declared-without-required",
        ),
        # Kept: required for a reason of its own.
        pytest.param(
            lambda: (
                kernel_spec(_selector(_open_selector_taking_pk, preconditions=[_wants_pk])).reads
            ),
            lambda: _own(_selector(_open_selector_taking_pk, preconditions=[_wants_pk])),
            "pk",
            False,
            id="input-required-beside-a-precondition",
        ),
        pytest.param(
            lambda: kernel_spec(_selector(_marked)).reads,
            lambda: _own(_selector(_marked)),
            "pk",
            False,
            id="input-required",
        ),
        pytest.param(
            lambda: kernel_spec(_selector(_keyed)).reads,
            lambda: _own(_selector(_keyed)),
            "kwargs",
            False,
            id="typed-dict-key",
        ),
        pytest.param(
            lambda: (
                kernel_spec(_selector(_by_status, reads=[UrlKwarg("status", required=True)])).reads
            ),
            lambda: _own(_selector(_by_status, reads=[UrlKwarg("status", required=True)])),
            "status",
            False,
            id="declared-by-the-selector-spec",
        ),
        pytest.param(
            lambda: (
                as_kernel_spec(
                    _spread_service(_spread, reads=[UrlKwarg("note", required=True)]), spread=True
                ).reads
            ),
            lambda: _own(
                _spread_service(_spread, reads=[UrlKwarg("note", required=True)]), spread=True
            ),
            "note",
            False,
            id="declared-by-the-service-spec",
        ),
        pytest.param(
            lambda: _target_reads(
                kernel_spec(
                    _lookup_service("instance", _lookup, reads=[UrlKwarg("pk", required=True)])
                )
            ),
            lambda: _lookup_s(
                _lookup_service("instance", _lookup, reads=[UrlKwarg("pk", required=True)])
            ),
            "pk",
            False,
            id="declared-by-the-spec-for-its-lookup",
        ),
        pytest.param(
            lambda: _target_reads(
                kernel_spec(
                    _lookup_service(
                        "instance", _lookup, lookup_reads=[UrlKwarg("pk", required=True)]
                    )
                )
            ),
            lambda: _lookup_s(
                _lookup_service("instance", _lookup, lookup_reads=[UrlKwarg("pk", required=True)])
            ),
            "pk",
            False,
            id="declared-by-the-lookup",
        ),
    ],
)
def test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for(
    compiled: Callable[[], Any], variant: Callable[[], Any], name: str, relaxed: bool
) -> None:
    assert compiled().get(name).required is True

    assert variant().get(name).required is not relaxed


def test_a_parameter_with_a_default_is_never_named() -> None:
    assert required_for_having_no_default(_selector(_defaulted)) == (
        frozenset({"status"}),
        frozenset(),
    )


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(_selector(lambda: None), id="no-read"),
        # Named, but already optional: the provider may fill it.
        pytest.param(_selector(_by_status, kwargs=lambda: {}), id="already-optional"),
        pytest.param(
            ServiceSpec(
                service=_untouched,
                permission_classes=[AllowAny],
                kwargs=lambda: {},
                instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
            ),
            id="lookup-already-optional",
        ),
    ],
)
def test_a_variant_with_nothing_to_relax_is_the_compiled_spec(spec: Any) -> None:
    assert kernel_spec_variant(spec, target=True) is kernel_spec(spec)
