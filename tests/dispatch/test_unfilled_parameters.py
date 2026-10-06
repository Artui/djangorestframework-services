"""A dispatched callable's parameter that nothing filled is refused, not crashed into.

A parameter with no default, absent from the assembled pool, used to reach the
callable and raise ``TypeError``: a declared optional read the caller did not
send, a ``kwargs=`` provider that declined it with ``UNSET``, or no provider at
all. Every transport built on ``dispatch_spec`` passed that crash on. An
``InputRequired`` key in the same position was already refused with
``ServiceValidationError``; these tests pin the unmarked parameter to the same
refusal, in the same shape, and pin what is *not* a missing argument: a reserved
seed, a defaulted parameter, ``**kwargs``, a positional-only parameter, a
``NotClientInput`` parameter, a parameter a ``functools.wraps`` wrapper fills
itself, a parameter of an affordance condition and one of the output re-read, none
of which a caller can fill.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from django.contrib.auth.models import User
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET, UnsetType
from rest_framework_services.views.mutation.service_create_view import ServiceCreateView
from rest_framework_services.views.mutation.service_update_view import ServiceUpdateView
from rest_framework_services.views.query.selector_retrieve_view import SelectorRetrieveView
from rest_framework_services.viewsets.service_viewset import ServiceViewSet
from tests.testapp.models import Post
from tests.testapp.serializers import PostSerializer

# A service runs atomically, and the async path opens its transaction off the loop.
pytestmark = pytest.mark.django_db(transaction=True)


class _Scope(TypedDict):
    tenant: str | UnsetType


def _declining_scope() -> _Scope:
    # Declines, as a provider does off HTTP where there is no tenant to read.
    return {"tenant": UNSET}


def _plain(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _archive(*, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


_TENANT_MISSING = {"non_field_errors": ["Missing required argument(s): 'tenant'."]}


def _selector(**kwargs: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_plain, **kwargs)


def _service(**kwargs: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=_archive, **kwargs)


_UNFILLED = [
    pytest.param(_selector(kwargs=_declining_scope), {"pk": 1}, id="selector-declining-provider"),
    pytest.param(_selector(), {"pk": 1}, id="selector-no-provider"),
    pytest.param(_service(kwargs=_declining_scope), {}, id="service-declining-provider"),
    pytest.param(_service(), {}, id="service-no-provider"),
]


@pytest.mark.parametrize(("spec", "params"), _UNFILLED)
def test_an_unfilled_parameter_is_refused_naming_it(spec: Any, params: dict[str, Any]) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params=params)
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize(("spec", "params"), _UNFILLED)
async def test_async_dispatch_refuses_an_unfilled_parameter_naming_it(
    spec: Any, params: dict[str, Any]
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params=params)
    assert excinfo.value.detail == _TENANT_MISSING


def _lookup(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _precondition(*, tenant: str) -> None: ...


_EVERY_CALLABLE = [
    pytest.param(
        ServiceSpec(
            service=_archive,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        ),
        id="target-lookup",
    ),
    pytest.param(
        ServiceSpec(service=lambda **_: None, preconditions=[_precondition]),
        id="precondition",
    ),
]


@pytest.mark.parametrize("spec", _EVERY_CALLABLE)
def test_every_callable_dispatch_resolves_is_refused_the_same_way(spec: Any) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize("spec", _EVERY_CALLABLE)
async def test_async_every_callable_dispatch_resolves_is_refused_the_same_way(
    spec: Any,
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


def test_the_caller_fills_what_the_provider_declined() -> None:
    # The decline is what lets the caller's value through; refusing it would undo
    # the reason a provider may decline at all.
    result = dispatch_spec(
        _selector(kwargs=_declining_scope), user=None, params={"pk": 1, "tenant": "acme"}
    )
    assert result.value == {"pk": 1, "tenant": "acme"}


def test_one_message_lists_every_missing_name_sorted_and_once() -> None:
    # ``marked`` is both InputRequired and default-less, so both checks find it;
    # it is named once. The order is the sort, not the signature's.
    def select(
        *, zeta: int, marked: Annotated[int, InputRequired], alpha: int, pk: int
    ) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == {
        "non_field_errors": ["Missing required argument(s): 'alpha', 'marked', 'zeta'."]
    }


def test_a_missing_reserved_seed_is_not_reported_as_an_argument() -> None:
    # ``data`` is stripped from caller input, so naming it would ask the caller for
    # a value dispatch would throw away. Only the name a caller can send is listed.
    def select(*, data: Any, tenant: str) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={})
    assert excinfo.value.detail == _TENANT_MISSING


def test_a_reserved_seed_alone_still_fails_as_the_author_error_it_is() -> None:
    # Requiring a seed is the author's error, so dispatch leaves it to the callable
    # rather than turn it into a caller's validation error.
    def archive(*, instance: Any) -> None: ...

    with pytest.raises(TypeError, match="instance"):
        dispatch_spec(ServiceSpec(service=archive), user=None, params={"instance": 1})


def test_a_defaulted_parameter_is_never_missing() -> None:
    def select(*, pk: int, tenant: str = "default") -> dict[str, Any]:
        return {"pk": pk, "tenant": tenant}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    assert dispatch_spec(spec, user=None, params={"pk": 1}).value == {
        "pk": 1,
        "tenant": "default",
    }


def test_var_keyword_is_never_missing() -> None:
    def select(*args: Any, **kwargs: Any) -> list[str]:
        return sorted(kwargs)

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=select)
    assert "tenant" not in dispatch_spec(spec, user=None, params={}).value


def test_a_positional_only_parameter_is_not_reported() -> None:
    # Dispatch passes keywords only, so no caller value could ever fill it, and
    # naming it would ask for one.
    def select(tenant: str, /) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=select)
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={})


def test_an_affordance_condition_is_not_asked_of_the_caller() -> None:
    # A condition sees only the seeds, never client input, so a caller sending
    # ``tenant`` cannot fill it. Refusing would name an argument the caller has
    # already sent; the author's condition fails as the configuration error it is.
    def when(*, tenant: str) -> bool:
        return True

    spec = ServiceSpec(
        service=_archive, affordances=[Affordance(code="open", reason="Closed.", when=when)]
    )
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(spec, user=None, params={"tenant": "acme"})


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()


def _create_plain(*, data: Any, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


def _create_marked(*, data: Any, tenant: Annotated[str, InputRequired]) -> dict[str, Any]:
    return {"tenant": tenant}


@pytest.mark.parametrize("create", [_create_plain, _create_marked], ids=["plain", "marked"])
def test_over_http_a_hook_that_leaves_a_parameter_out_is_refused_too(create: Any) -> None:
    # HTTP views dispatch through the same core. ``as_view()`` refuses a parameter
    # nothing could feed; with a hook declared it cannot tell, so a hook that leaves
    # the parameter out reaches dispatch, which answers the validation error rather
    # than a server error. The marked case is the ``InputRequired`` check, which
    # has always run here too.
    viewset = type(
        "_VS",
        (ServiceViewSet,),
        {
            "queryset": User.objects.all(),
            "action_specs": {"create": ServiceSpec(service=create, input_serializer=_TitleInput)},
            "get_service_kwargs": lambda self: {},
        },
    )
    response = viewset.as_view({"post": "create"})(
        APIRequestFactory().post("/x/", {"title": "t"}, format="json")
    )
    assert response.status_code == 400
    assert response.data == _TENANT_MISSING


def test_a_positional_or_keyword_parameter_is_missing_too() -> None:
    # Every other unfilled parameter in this file is keyword-only, so this is the
    # one that holds the other passable kind.
    def select(pk: int, tenant: str) -> dict[str, Any]:
        return {"pk": pk, "tenant": tenant}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


def test_a_parameter_beside_var_keyword_is_still_missing() -> None:
    # ``**kwargs`` takes what arrives, but nothing arriving fills ``tenant``: the
    # callable that runs is the one declaring it, so the call would fail.
    def select(*, pk: int, tenant: str, **extras: Any) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


# --- a parameter the caller is never asked for --------------------------------------


def test_a_hidden_parameter_nothing_filled_is_not_named_as_missing() -> None:
    # Naming ``role`` would tell the caller to send it, and ``REJECT`` then refuses
    # it as unexpected. Nothing the caller sends can fill it, so it stays the
    # author's ``TypeError``.
    def select(*, pk: int, role: Annotated[str, NotClientInput]) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(TypeError, match="role"):
        dispatch_spec(spec, user=None, params={"pk": 1}, unknown_arguments=UnknownArguments.REJECT)


def test_a_hidden_parameter_is_left_out_of_a_refusal_naming_another() -> None:
    def select(*, pk: int, tenant: str, role: Annotated[str, NotClientInput]) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


# --- a wrapper that fills the parameter itself --------------------------------------


def _injecting_tenant(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A decorator that supplies ``tenant`` itself, as a scoping decorator does.

    ``functools.wraps`` makes ``inspect.signature`` report ``fn``'s parameters, so
    ``tenant`` reads as required, while the wrapper is what is called and it fills
    ``tenant`` before ``fn`` sees the call.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("tenant", "injected")
        return fn(*args, **kwargs)

    return wrapper


_INJECTED: list[str] = []


@_injecting_tenant
def _scoped(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


@_injecting_tenant
def _scoped_post(*, pk: int, tenant: str) -> QuerySet[Post]:
    _INJECTED.append(tenant)
    return Post.objects.filter(pk=pk)


@_injecting_tenant
def _scoped_create(*, data: Any, tenant: str) -> dict[str, Any]:
    return {"tenant": tenant}


def test_a_wrapper_that_fills_the_parameter_itself_is_not_refused() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped)
    assert dispatch_spec(spec, user=None, params={"pk": 1}).value == {
        "pk": 1,
        "tenant": "injected",
    }


async def test_async_a_wrapper_that_fills_the_parameter_itself_is_not_refused() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped)
    result = await adispatch_spec(spec, user=None, params={"pk": 1})
    assert result.value == {"pk": 1, "tenant": "injected"}


def test_over_http_a_selector_view_runs_a_wrapper_that_fills_the_parameter() -> None:
    post = Post.objects.create(title="p")
    _INJECTED.clear()

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_scoped_post, output_serializer=PostSerializer
        )

    response = _View.as_view()(APIRequestFactory().get("/"), pk=post.pk)
    assert response.status_code == 200
    assert response.data["id"] == post.pk
    assert _INJECTED == ["injected"]


def test_over_http_a_create_runs_a_wrapper_that_fills_the_parameter() -> None:
    viewset = type(
        "_VS",
        (ServiceViewSet,),
        {
            "queryset": User.objects.all(),
            "action_specs": {
                "create": ServiceSpec(service=_scoped_create, input_serializer=_TitleInput)
            },
            "get_service_kwargs": lambda self: {},
        },
    )
    response = viewset.as_view({"post": "create"})(
        APIRequestFactory().post("/x/", {"title": "t"}, format="json")
    )
    assert response.status_code == 201
    assert response.data == {"tenant": "injected"}


def test_a_wrapper_naming_its_own_parameters_is_still_refused() -> None:
    # The wrapper differs from what it wraps (``trace``), but takes no ``**kwargs``,
    # so it cannot fill ``tenant``: only what dispatch passes reaches it.
    def select(*, pk: int, tenant: str) -> None: ...

    @functools.wraps(select)
    def traced(*, pk: int, tenant: str, trace: bool = False) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=traced)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


def test_a_staticmethod_is_read_through_not_skipped() -> None:
    # A ``staticmethod`` object carries ``__wrapped__`` too, but it is not a wrapper
    # that could fill anything: calling it calls ``_plain`` with what dispatch passes.
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=staticmethod(_plain))
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params={"pk": 1})
    assert excinfo.value.detail == _TENANT_MISSING


# --- the output re-read, after the write committed ----------------------------------


def _create_post() -> Post:
    return Post.objects.create(title="created")


def _reread(*, result: Post, tenant: str) -> QuerySet[Post]:
    return Post.objects.filter(pk=result.pk)


_CREATE_THEN_REREAD = ServiceSpec(
    service=_create_post,
    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_reread),
)


def test_the_output_re_read_is_not_refused_after_the_write_committed() -> None:
    # The row is written before the re-read runs. A validation error would tell the
    # caller nothing happened, and a caller that retries on one writes it twice; no
    # caller input reaches this pool, so ``tenant`` is the author's to supply.
    with pytest.raises(TypeError, match="tenant"):
        dispatch_spec(_CREATE_THEN_REREAD, user=None, params={})
    assert Post.objects.count() == 1


async def test_async_the_output_re_read_is_not_refused_after_the_write_committed() -> None:
    with pytest.raises(TypeError, match="tenant"):
        await adispatch_spec(_CREATE_THEN_REREAD, user=None, params={})
    assert await Post.objects.acount() == 1


def test_over_http_the_output_re_read_is_not_refused_either() -> None:
    """With a hook declared, ``as_view()`` cannot tell what feeds the re-read, so
    the view is served; the hook's kwargs never reach the re-read's pool."""

    class _View(ServiceCreateView):
        spec = _CREATE_THEN_REREAD

        def get_service_kwargs(self) -> dict[str, Any]:
            return {}

    with pytest.raises(TypeError, match="tenant"):
        _View.as_view()(APIRequestFactory().post("/", {}, format="json"))
    assert Post.objects.count() == 1


# --- what the refusal answers over HTTP ---------------------------------------------


def _post_for_tenant(*, pk: int, tenant: str) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post) -> Post:
    return instance


def test_over_http_a_lookup_parameter_nothing_fills_is_a_400() -> None:
    """``as_view()`` never checks a target lookup's parameters, so this reaches
    dispatch with no hook or provider declared, and the mutation view maps the
    refusal as it maps any validation error."""

    class _View(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(
            service=_touch,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_for_tenant
            ),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=PostSerializer
            ),
        )

    post = Post.objects.create(title="p")
    response = _View.as_view()(APIRequestFactory().put("/", {}, format="json"), pk=post.pk)
    assert response.status_code == 400
    assert response.data == _TENANT_MISSING


def test_over_http_a_selector_view_still_answers_a_server_error() -> None:
    """No selector view maps ``ServiceValidationError``, so it escapes the view as
    the ``TypeError`` did, and Django answers ``500``."""

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_post_for_tenant, output_serializer=PostSerializer
        )

    post = Post.objects.create(title="p")
    with pytest.raises(ServiceValidationError):
        _View.as_view()(APIRequestFactory().get("/"), pk=post.pk)
