"""``translating_drf_refusals`` changes what an author callable raises, and nothing the compile reads.

The compile hands the kernel each author callable that runs within a call
through it. The kernel binds the call by the callable's signature, awaits it
by its kind, and reads the keys a ``kwargs`` provider fills off its return
``TypedDict``, resolved in the provider's own module. So every surface the
compile derives is compared with the translator patched out: a wrapper that
hid ``async``-ness, the signature, the annotations or ``__wrapped__`` would
change one of them.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from django.db.models import QuerySet
from django_service_specs.pool.provider_keys import provider_keys
from django_service_specs.types.is_async import is_async
from rest_framework import serializers
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    NotClientInput,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)
from rest_framework_services.dispatch.utils import server_owned_keys
from rest_framework_services.service_specs import as_kernel_spec as compiler
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.service_specs.translating_drf_refusals import (
    translating_drf_refusals,
)
from tests.testapp.models import Post


class _Scope(TypedDict):
    tenant: str


class _Keys(TypedDict):
    title: str
    status: str


class _Title(serializers.Serializer):
    title = serializers.CharField()
    status = serializers.CharField()
    note = serializers.CharField(required=False)


def _scope(**_: Any) -> _Scope:
    return {"tenant": "acme"}


def _create(*, title: str, status: str, tenant: str) -> None: ...


async def _acreate(*, title: str, status: str, tenant: str) -> None: ...


def _gate(*, tenant: Annotated[str, NotClientInput] = "own", status: str) -> None: ...


def _spread(
    *, title: str, priority: int = 1, owner: Annotated[str, NotClientInput] = "x"
) -> None: ...


def _unpacked(**keys: Unpack[_Keys]) -> None: ...


def _rows(*, q: str = "", tenant: Annotated[str, NotClientInput] = "own") -> QuerySet[Post]:
    return Post.objects.all()


async def _arows(*, q: str = "") -> QuerySet[Post]:
    return Post.objects.all()


def _lookup(*, pk: int, tenant: Annotated[str, NotClientInput] = "own") -> Post:
    return Post.objects.get(pk=pk)


def _extend(queryset: Any, view: Any, request: Any) -> Any:
    return queryset


_SPECS: dict[str, Callable[[], Any]] = {
    "sync-service": lambda: ServiceSpec(
        service=_create,
        input_serializer=_Title,
        kwargs=_scope,
        preconditions=[_gate],
        input_data=lambda **_: {},
    ),
    "async-service": lambda: ServiceSpec(service=_acreate, input_serializer=_Title, kwargs=_scope),
    "spread-service": lambda: ServiceSpec(service=_spread),
    "unpacked-service": lambda: ServiceSpec(service=_unpacked),
    "update-with-lookup": lambda: ServiceSpec(
        service=_create,
        input_serializer=_Title,
        kwargs=_scope,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, kwargs=_scope
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, extend_queryset=_extend
        ),
    ),
    "list-selector": lambda: SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        kwargs=_scope,
        preconditions=[_gate],
        extend_queryset=_extend,
        reads=[QueryParam("status")],
    ),
    "async-selector": lambda: SelectorSpec(kind=SelectorKind.LIST, selector=_arows),
}
_VARIANTS: list[dict[str, Any]] = [
    {},
    {"lenient": True},
    {"spread": True},
    {"server_input": False},
    {"unguarded": True},
    {"target": True},
]


def _surfaces(spec: Any) -> list[Any]:
    """Everything the compile derives that a translator could change."""
    compiled = spec.as_kernel_spec()
    callables = [
        getattr(compiled, "service", None) or compiled.selector,
        *(compiled.preconditions or ()),
    ]
    return [
        compiled.parameters(),
        server_owned_keys(spec),
        provider_keys(compiled.kwargs),
        [is_async(fn) for fn in callables],
        [inspect.signature(fn) for fn in callables],
        *(kernel_spec_variant(spec, **variant).parameters() for variant in _VARIANTS),
    ]


@pytest.mark.parametrize("build", list(_SPECS.values()), ids=list(_SPECS))
def test_the_compile_reads_the_same_with_and_without_it(
    build: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh spec for each compile: a spec caches what it compiles to.
    translated = _surfaces(build())
    monkeypatch.setattr(compiler, "translating_drf_refusals", lambda fn: fn)

    assert translated == _surfaces(build())


def test_a_typed_provider_still_names_the_keys_it_fills() -> None:
    """Resolved in the provider's own module, where the ``TypedDict`` is."""
    assert provider_keys(translating_drf_refusals(_scope)) == provider_keys(_scope)
    assert provider_keys(translating_drf_refusals(_scope)).filled == {"tenant"}  # ty: ignore[unresolved-attribute]


class _Bound:
    def create(self, *, title: str, status: str, tenant: str) -> None: ...


@pytest.mark.parametrize(
    "fn", [_create, _acreate, _Bound().create], ids=["sync", "async", "method"]
)
def test_it_reports_what_it_wraps(fn: Callable[..., Any]) -> None:
    wrapped = translating_drf_refusals(fn)

    assert is_async(wrapped) is is_async(fn)
    assert inspect.signature(wrapped) == inspect.signature(fn)
    assert wrapped.__wrapped__ is fn  # ty: ignore[unresolved-attribute]
    assert (wrapped.__name__, wrapped.__qualname__, wrapped.__module__) == (
        fn.__name__,
        fn.__qualname__,
        fn.__module__,
    )
    assert wrapped.__annotations__ == fn.__annotations__
    assert fn.__dict__ == {}


def test_a_callable_with_no_name_of_its_own_is_named_for_what_it_is() -> None:
    wrapped = translating_drf_refusals(functools.partial(_create, title="t"))

    assert (wrapped.__name__, wrapped.__qualname__) == ("callable", "callable")
    assert inspect.signature(wrapped) == inspect.signature(functools.partial(_create, title="t"))


def test_an_empty_seat_stays_empty() -> None:
    assert translating_drf_refusals(None) is None


# --- What a reader of the compiled spec's callables sees ---------------------


def _documented_service(*, title: str = "") -> None:
    """The service's own words."""


_documented_service.audience = "staff"  # ty: ignore[unresolved-attribute]


async def _documented_aservice(*, title: str = "") -> None:
    """The async service's own words."""


def _documented_selector(**_: Any) -> QuerySet[Post]:
    """The selector's own words."""
    return Post.objects.all()


def _documented_kwargs(**_: Any) -> _Scope:
    """The provider's own words."""
    return {"tenant": "acme"}


class _Owner:
    def create(self, *, title: str = "") -> None:
        """The method's own words."""

    def rows(self, **_: Any) -> QuerySet[Post]:
        """The bound selector's own words."""
        return Post.objects.all()


_DOCUMENTED: dict[str, tuple[Callable[[], Any], Callable[[Any], Any], str]] = {
    "function-service": (
        lambda: ServiceSpec(service=_documented_service),
        lambda compiled: compiled.service,
        "The service's own words.",
    ),
    "method-service": (
        lambda: ServiceSpec(service=_Owner().create),
        lambda compiled: compiled.service,
        "The method's own words.",
    ),
    "async-service": (
        lambda: ServiceSpec(service=_documented_aservice),
        lambda compiled: compiled.service,
        "The async service's own words.",
    ),
    "function-selector": (
        lambda: SelectorSpec(kind=SelectorKind.LIST, selector=_documented_selector),
        lambda compiled: compiled.selector,
        "The selector's own words.",
    ),
    "method-selector": (
        lambda: SelectorSpec(kind=SelectorKind.LIST, selector=_Owner().rows),
        lambda compiled: compiled.selector,
        "The bound selector's own words.",
    ),
    "kwargs-provider": (
        lambda: ServiceSpec(service=_documented_service, kwargs=_documented_kwargs),
        lambda compiled: compiled.kwargs,
        "The provider's own words.",
    ),
}


@pytest.mark.parametrize(
    ("build", "seat", "words"), list(_DOCUMENTED.values()), ids=list(_DOCUMENTED)
)
def test_the_compiled_callable_keeps_the_author_s_docstring(
    build: Callable[[], Any], seat: Callable[[Any], Any], words: str
) -> None:
    """A tool description is read off the compiled callable, as on 0.61.0."""
    assert inspect.getdoc(seat(build().as_kernel_spec())) == words


def test_a_function_s_attributes_survive_the_wrap() -> None:
    wrapped = translating_drf_refusals(_documented_service)

    assert wrapped.audience == "staff"  # ty: ignore[unresolved-attribute]
    assert _documented_service.__dict__ == {"audience": "staff"}


def test_a_decorated_function_is_still_wrapped_by_the_translator() -> None:
    """Its own ``__wrapped__`` and ``__signature__`` attributes do not displace the translator's."""

    def inner(*, title: str) -> None: ...

    @functools.wraps(inner)
    def decorated(**kwargs: Any) -> None: ...

    wrapped = translating_drf_refusals(decorated)

    assert wrapped.__wrapped__ is decorated  # ty: ignore[unresolved-attribute]
    assert inspect.unwrap(wrapped) is inner
    assert inspect.signature(wrapped) == inspect.signature(decorated)


class _Callable:
    """The class's words, which a partial or an object has no function to read."""

    def __init__(self) -> None:
        self.note = "kept on the object"

    def __call__(self, *, title: str = "") -> None: ...


def _a_partial_with_an_attribute() -> Callable[..., Any]:
    partial = functools.partial(_create, title="t")
    partial.note = "kept on the partial"  # ty: ignore[unresolved-attribute]
    return partial


@pytest.mark.parametrize(
    "author",
    [_a_partial_with_an_attribute(), _Callable()],
    ids=["partial", "callable-object"],
)
def test_a_partial_or_a_callable_object_gets_no_docstring_or_attributes(
    author: Callable[..., Any],
) -> None:
    """``inspect.isfunction`` is how a reader tells a function's words from a class's.

    Copying the class's docstring would make ``functools.partial`` or the
    callable's class read as the description of this one callable, and copying
    the attributes would stamp the wrapper with what belongs to the object.
    Each is held apart: the docstring by ``__doc__``, the attributes by a
    ``note`` the author carries on the instance.
    """
    assert author.note  # ty: ignore[unresolved-attribute]

    wrapped = translating_drf_refusals(author)

    assert wrapped.__doc__ is None
    assert inspect.getdoc(wrapped) is None
    assert "note" not in wrapped.__dict__
    assert inspect.unwrap(wrapped) is author
