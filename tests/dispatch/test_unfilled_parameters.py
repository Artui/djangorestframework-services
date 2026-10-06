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
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict, Unpack

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.argument_binding import ArgumentBinding
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


# A service's parameters are the caller's to fill only where its input is spread.
_SPREAD = ArgumentBinding.SPREAD_AUTHOR_WINS

_UNFILLED = [
    pytest.param(
        _selector(kwargs=_declining_scope),
        {"pk": 1},
        ArgumentBinding.AUTO,
        id="selector-declining-provider",
    ),
    pytest.param(_selector(), {"pk": 1}, ArgumentBinding.AUTO, id="selector-no-provider"),
    pytest.param(
        _service(kwargs=_declining_scope), {}, _SPREAD, id="spread-service-declining-provider"
    ),
    pytest.param(_service(), {}, _SPREAD, id="spread-service-no-provider"),
]


@pytest.mark.parametrize(("spec", "params", "binding"), _UNFILLED)
def test_an_unfilled_parameter_is_refused_naming_it(
    spec: Any, params: dict[str, Any], binding: ArgumentBinding
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch_spec(spec, user=None, params=params, argument_binding=binding)
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize(("spec", "params", "binding"), _UNFILLED)
async def test_async_dispatch_refuses_an_unfilled_parameter_naming_it(
    spec: Any, params: dict[str, Any], binding: ArgumentBinding
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params=params, argument_binding=binding)
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
        dispatch_spec(spec, user=None, params={"pk": 1}, argument_binding=_SPREAD)
    assert excinfo.value.detail == _TENANT_MISSING


@pytest.mark.parametrize("spec", _EVERY_CALLABLE)
async def test_async_every_callable_dispatch_resolves_is_refused_the_same_way(
    spec: Any,
) -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        await adispatch_spec(spec, user=None, params={"pk": 1}, argument_binding=_SPREAD)
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


def _viewset_whose_hook_leaves_tenant_out(create: Any) -> Any:
    return type(
        "_VS",
        (ServiceViewSet,),
        {
            "queryset": User.objects.all(),
            "action_specs": {"create": ServiceSpec(service=create, input_serializer=_TitleInput)},
            "get_service_kwargs": lambda self: {},
        },
    )


def test_over_http_a_hook_that_leaves_a_parameter_out_is_a_server_error() -> None:
    """HTTP views dispatch through the same core. ``as_view()`` refuses a parameter
    nothing could feed; with a hook declared it cannot tell, so a hook that leaves
    the parameter out reaches dispatch. A mutation dispatches its service
    ``BUNDLE``, so no request body could fill ``tenant``: naming it would send the
    client to resend a field that never arrives. It is the server's gap, and fails
    as the callable's ``TypeError``, a ``500``."""
    view = _viewset_whose_hook_leaves_tenant_out(_create_plain).as_view({"post": "create"})

    with pytest.raises(TypeError, match="tenant"):
        view(APIRequestFactory().post("/x/", {"title": "t", "tenant": "acme"}, format="json"))


def test_over_http_a_hook_that_leaves_an_input_required_key_out_is_a_server_error() -> None:
    """The ``InputRequired`` check asks the same question: the marker says the value
    must arrive, but no request body reaches a ``BUNDLE`` service by name, so a
    ``400`` naming ``tenant`` would send the client to resend a field that never
    arrives. It is the server's gap, a ``500``, as for an unmarked parameter."""
    view = _viewset_whose_hook_leaves_tenant_out(_create_marked).as_view({"post": "create"})

    with pytest.raises(TypeError, match="tenant"):
        view(APIRequestFactory().post("/x/", {"title": "t", "tenant": "acme"}, format="json"))


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
    """A selector view dispatches ``BUNDLE``, so no request fills ``tenant`` by name
    and it is not asked of the caller: it fails as the selector's ``TypeError``, and
    Django answers ``500``, as before the check existed."""

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_post_for_tenant, output_serializer=PostSerializer
        )

    post = Post.objects.create(title="p")
    for query in ({}, {"tenant": "acme"}):
        with pytest.raises(TypeError, match="tenant"):
            _View.as_view()(APIRequestFactory().get("/", query), pk=post.pk)


# --- only a name the caller could send is named ----------------------------------
#
# A refusal tells the client what to send, and a client that retries on a validation
# error, as an agent does, sends it. A name it cannot send, or has already sent,
# would be asked for again forever. So each name refused here is one the resend
# delivers, under every policy, ``REJECT`` included; anything else unfilled is the
# server's gap and fails as the callable's own ``TypeError``.


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_POLICIES = pytest.mark.parametrize(
    "policy", list(UnknownArguments), ids=[policy.name.lower() for policy in UnknownArguments]
)


def _create_for_tenant(*, data: Any, tenant: str) -> str:
    return tenant


@_CORES
@_POLICIES
def test_a_bundled_service_parameter_is_the_author_s_error(
    dispatch: Any, policy: UnknownArguments
) -> None:
    """Under ``BUNDLE`` the caller's input reaches the service only as ``data``, so a
    resend carrying ``tenant`` could never fill the parameter. Naming it sent an
    agent round the same refusal forever under ``IGNORE`` and ``PASSTHROUGH``, and
    into ``Unexpected argument(s)`` under ``REJECT``."""
    spec = ServiceSpec(
        service=_create_for_tenant, input_serializer=_TitleInput, kwargs=_declining_scope
    )

    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={"title": "t"}, unknown_arguments=policy)


class _NoteInput(serializers.Serializer):
    note = serializers.CharField(required=False)


def _create_with_note(*, data: Any, note: str) -> str:
    return note


@_CORES
@_POLICIES
def test_a_bundled_service_parameter_named_like_a_field_is_the_author_s_error(
    dispatch: Any, policy: UnknownArguments
) -> None:
    """``note`` is declared input, a serializer field the caller left out, but under
    ``BUNDLE`` the field reaches the service only inside ``data``: the resend below
    carries it and the parameter is still unfilled. So naming it would ask for a
    value that cannot arrive, and it is the author's ``TypeError``, as every gap
    under ``BUNDLE`` is."""
    spec = ServiceSpec(service=_create_with_note, input_serializer=_NoteInput)

    with pytest.raises(TypeError, match="note"):
        dispatch(spec, params={}, unknown_arguments=policy)
    with pytest.raises(TypeError, match="note"):
        dispatch(spec, params={"note": "n"}, unknown_arguments=policy)


def _rows_for(*, tenant: str) -> list[str]:
    return [tenant]


def _tenant_rows_or_none(*, tenant: str = "none") -> list[str]:
    return [tenant]


_BUNDLED_SELECTORS = [
    pytest.param(SelectorSpec(kind=SelectorKind.LIST, selector=_rows_for), id="selector"),
    pytest.param(
        SelectorSpec(
            kind=SelectorKind.LIST, selector=_tenant_rows_or_none, preconditions=[_precondition]
        ),
        id="selector-precondition",
    ),
]


@_CORES
@_POLICIES
@pytest.mark.parametrize("spec", _BUNDLED_SELECTORS)
def test_a_bundled_selector_parameter_is_the_author_s_error(
    dispatch: Any, policy: UnknownArguments, spec: Any
) -> None:
    """``BUNDLE`` spreads no caller input into a selector's pool either, so the
    resend below carries ``tenant`` and the parameter is still unfilled. Naming it
    on the first answer sent a client to send a key that never arrives, so under
    ``BUNDLE`` nothing is the caller's to fill, for any spec. Fails if
    ``caller_fillable`` reads a selector's declared input whatever the binding."""
    for sent in ({}, {"tenant": "acme"}):
        with pytest.raises(TypeError, match="tenant"):
            dispatch(
                spec, params=sent, argument_binding=ArgumentBinding.BUNDLE, unknown_arguments=policy
            )


class _MarkedScope(TypedDict, total=False):
    tenant: Annotated[str, InputRequired]


def _create_with_marked_scope(*, data: Any = None, **scope: Unpack[_MarkedScope]) -> str:
    return scope["tenant"]


def _marked_gate(*, tenant: Annotated[str, InputRequired]) -> None: ...


def _marked_rows(*, tenant: Annotated[str, InputRequired]) -> list[str]:
    return [tenant]


_BUNDLED_INPUT_REQUIRED = [
    pytest.param(
        ServiceSpec(service=_create_marked, input_serializer=_TitleInput),
        ArgumentBinding.AUTO,
        {"title": "t"},
        TypeError,
        id="service-parameter",
    ),
    pytest.param(
        ServiceSpec(service=_create_with_marked_scope),
        ArgumentBinding.AUTO,
        {},
        KeyError,
        id="service-typed-dict-key",
    ),
    pytest.param(
        ServiceSpec(
            service=lambda *, data: None, input_serializer=_TitleInput, preconditions=[_marked_gate]
        ),
        ArgumentBinding.AUTO,
        {"title": "t"},
        TypeError,
        id="service-precondition",
    ),
    pytest.param(
        SelectorSpec(kind=SelectorKind.LIST, selector=_marked_rows),
        ArgumentBinding.BUNDLE,
        {},
        TypeError,
        id="selector",
    ),
]


def _open_rows(**filters: Any) -> list[str]:
    return [filters.get("tenant", "")]


def _scope_gate(**scope: Unpack[_MarkedScope]) -> None:
    assert scope["tenant"] == "acme"


@_CORES
@_POLICIES
def test_an_open_selector_still_refuses_a_marked_typed_dict_key(
    dispatch: Any, policy: UnknownArguments
) -> None:
    """The ``InputRequired`` key sits inside a precondition's ``**scope``, which no
    signature names as a parameter, beside a selector whose bare ``**filters``
    opens the call. Open, every name is the caller's to send, so ``tenant`` is
    named, and the resend reaches both. Fails if an open call site admits only
    the parameters a signature names."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_open_rows, preconditions=[_scope_gate])

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={}, unknown_arguments=policy)
    assert excinfo.value.detail == _TENANT_MISSING
    assert dispatch(spec, params={"tenant": "acme"}, unknown_arguments=policy).value == ["acme"]


@_CORES
@_POLICIES
@pytest.mark.parametrize(("spec", "binding", "params", "error"), _BUNDLED_INPUT_REQUIRED)
def test_a_bundled_input_required_key_is_the_author_s_error(
    dispatch: Any,
    policy: UnknownArguments,
    spec: Any,
    binding: ArgumentBinding,
    params: dict[str, Any],
    error: type[Exception],
) -> None:
    """The marker says the value must arrive, but under ``BUNDLE`` no resend can
    deliver it by name: the same refusal came back, or ``REJECT`` refused the key
    as unexpected. So the ``InputRequired`` check asks only what the caller could
    fill, as the unfilled-parameter check does, and a key nothing fills here is the
    author's error. A named parameter fails as the callable's ``TypeError``, and a
    ``TypedDict`` key as whatever reading it raises. Fails if the marked keys are
    not passed through ``Fillable.admits``."""
    with pytest.raises(error, match="tenant"):
        dispatch(spec, params=params, argument_binding=binding, unknown_arguments=policy)


def _reread_marked(*, result: Post, tenant: Annotated[str, InputRequired]) -> QuerySet[Post]:
    return Post.objects.filter(pk=result.pk)


def _marked_condition(*, tenant: Annotated[str, InputRequired]) -> bool:
    return True


_UNASKED_MARKED = [
    pytest.param(
        ServiceSpec(
            service=_create_post,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_reread_marked),
        ),
        1,
        id="output-re-read",
    ),
    pytest.param(
        ServiceSpec(
            service=_create_post,
            affordances=[Affordance(code="open", reason="Closed.", when=_marked_condition)],
        ),
        0,
        id="affordance-condition",
    ),
]


@_CORES
@pytest.mark.parametrize(("spec", "written"), _UNASKED_MARKED)
def test_a_marked_key_no_caller_input_reaches_is_not_refused(
    dispatch: Any, spec: Any, written: int
) -> None:
    """No caller input reaches either pool, so a marked key there is the author's to
    supply too. For the re-read it matters most: the row is written first, and a
    refusal would report a write that happened as one that did not."""
    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={"tenant": "acme"}, argument_binding=_SPREAD)
    assert Post.objects.count() == written


def _defaulted_tenant(*, pk: int, tenant: str = "none") -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _close_for_tenant(*, reason: str, tenant: str) -> dict[str, str]:
    return {"reason": reason, "tenant": tenant}


def _open_service(**changes: Any) -> str:
    return "ok"


def _post_scoped(*, pk: int, tenant: str) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _keep(*, instance: Post) -> int:
    return instance.pk


def _rows_scoped(*, tenant: str) -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _count_rows(*, collection: QuerySet[Post]) -> int:
    return collection.count()


_NAMED = [
    pytest.param(
        _selector(kwargs=_declining_scope), ArgumentBinding.AUTO, {"pk": 1}, id="selector"
    ),
    pytest.param(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_defaulted_tenant, preconditions=[_precondition]
        ),
        ArgumentBinding.AUTO,
        {"pk": 1},
        id="selector-precondition",
    ),
    pytest.param(
        ServiceSpec(service=_close_for_tenant, kwargs=_declining_scope),
        _SPREAD,
        {"reason": "r"},
        id="spread-service",
    ),
    pytest.param(
        ServiceSpec(service=_open_service, preconditions=[_precondition]),
        _SPREAD,
        {},
        id="open-spread-service-precondition",
    ),
    pytest.param(
        ServiceSpec(
            service=_keep,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_scoped),
        ),
        ArgumentBinding.AUTO,
        {"pk": None},
        id="target-lookup",
    ),
    pytest.param(
        ServiceSpec(
            service=_count_rows,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows_scoped),
        ),
        ArgumentBinding.AUTO,
        {},
        id="collection-lookup",
    ),
]


@_CORES
@_POLICIES
@pytest.mark.parametrize(("spec", "binding", "params"), _NAMED)
def test_a_refused_name_is_one_the_callers_resend_delivers(
    dispatch: Any,
    policy: UnknownArguments,
    spec: Any,
    binding: ArgumentBinding,
    params: dict[str, Any],
) -> None:
    """The ``open-spread-service-precondition`` row holds the open reading: with a
    bare ``**changes`` every name the precondition takes is one the caller could
    send, so ``tenant`` is named rather than left to the ``TypeError``."""
    post = Post.objects.create(title="p")
    sent = {key: post.pk if key == "pk" else value for key, value in params.items()}

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params=sent, argument_binding=binding, unknown_arguments=policy)
    assert excinfo.value.detail == _TENANT_MISSING

    dispatch(
        spec,
        params={**sent, "tenant": "acme"},
        argument_binding=binding,
        unknown_arguments=policy,
    )


def _close_for_reason(*, reason: str) -> str:
    return reason


def _region_gate(*, region: str) -> None: ...


@_CORES
def test_a_precondition_parameter_the_service_does_not_declare_is_the_author_s_error(
    dispatch: Any,
) -> None:
    """``region`` is not the closed service's input, so ``REJECT`` would refuse the
    resend and the schema does not list it: it is the server's to fill."""
    spec = ServiceSpec(service=_close_for_reason, preconditions=[_region_gate])

    with pytest.raises(TypeError, match="region"):
        dispatch(spec, params={"reason": "r"}, argument_binding=_SPREAD)


@_CORES
def test_a_selector_precondition_parameter_the_selector_does_not_declare_is_the_author_s_error(
    dispatch: Any,
) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_defaulted_tenant, preconditions=[_region_gate]
    )

    with pytest.raises(TypeError, match="region"):
        dispatch(spec, params={"pk": 1})


def _hidden_gate(*, tenant: Annotated[str, NotClientInput]) -> None: ...


@_CORES
def test_a_hidden_parameter_of_an_open_call_is_not_named(dispatch: Any) -> None:
    """Open, every name is the caller's but the server-owned ones: a precondition's
    hidden ``tenant`` is dropped from what the caller sends, so naming it would ask
    for a value dispatch then throws away."""
    spec = ServiceSpec(service=_open_service, preconditions=[_hidden_gate])

    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={}, argument_binding=_SPREAD)


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _retitle_with_pk(*, instance: Post, data: Any, pk: int) -> int:
    return pk


@_CORES
def test_a_sent_key_the_service_never_receives_is_not_named(dispatch: Any) -> None:
    """``pk`` is declared, as the lookup's key, and the caller sent it, but with an
    input serializer the service is spread only what the serializer validated. A
    resend would carry the same ``pk`` to the same end, so it is not named."""
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_retitle_with_pk,
        input_serializer=_TitleInput,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    )

    with pytest.raises(TypeError, match="pk"):
        dispatch(spec, params={"pk": post.pk, "title": "t"}, argument_binding=_SPREAD)


def _retitle(*, instance: Post, title: str) -> str:
    return title


def _open_retitle(*, instance: Post, **changes: Any) -> str:
    return "ok"


def _pk_gate(*, pk: int) -> None: ...


_BY_PK = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)

_LOOKUP_KEY_NEVER_HANDED_ON = [
    pytest.param(
        ServiceSpec(
            service=_retitle_with_pk, input_serializer=_TitleInput, instance_selector_spec=_BY_PK
        ),
        id="serializer-service-naming-the-lookups-key",
    ),
    pytest.param(
        ServiceSpec(service=_retitle, preconditions=[_pk_gate], instance_selector_spec=_BY_PK),
        id="closed-service-precondition-naming-the-lookups-key",
    ),
    pytest.param(
        ServiceSpec(service=_open_retitle, preconditions=[_pk_gate], instance_selector_spec=_BY_PK),
        id="open-service-precondition-naming-the-lookups-key",
    ),
]


@_CORES
@_POLICIES
@pytest.mark.parametrize("spec", _LOOKUP_KEY_NEVER_HANDED_ON)
def test_a_lookup_key_the_service_is_never_handed_is_not_named(
    dispatch: Any, policy: UnknownArguments, spec: Any
) -> None:
    """The route fills the lookup's ``pk``, and ``service_extras`` hands the
    lookup's keys on to the service, and so to its preconditions, only by name:
    none of them beside an input serializer, and otherwise only those the service
    names. ``pk`` is declared input, as the lookup's key, but a resend carrying it
    leaves the service's or the precondition's ``pk`` as unfilled as before, so the
    first answer is the callable's ``TypeError`` rather than a refusal naming
    ``pk`` that the resend answers with the same error."""
    post = Post.objects.create(title="p")
    route = build_offline_context(None, kwargs={"pk": post.pk})

    for params in ({"title": "t"}, {"title": "t", "pk": post.pk}):
        with pytest.raises(TypeError, match="'pk'"):
            dispatch(
                spec,
                params=params,
                argument_binding=_SPREAD,
                unknown_arguments=policy,
                request=route.request,
                view=route.view,
            )


def _retitle_naming_pk(*, instance: Post, title: str, pk: int) -> int:
    return pk


def _open_retitle_naming_pk(*, instance: Post, pk: int, **changes: Any) -> int:
    return pk


@_CORES
@pytest.mark.parametrize(
    "service",
    [
        pytest.param(_retitle_naming_pk, id="closed-service"),
        pytest.param(_open_retitle_naming_pk, id="open-service"),
    ],
)
def test_a_lookup_key_the_service_names_is_still_named(dispatch: Any, service: Any) -> None:
    """With no input serializer a spread service is handed the lookup's keys it
    names, closed or beside a bare ``**changes``, so a ``pk`` the route filled for
    the lookup alone is the caller's to send, and the resend delivers it."""
    post = Post.objects.create(title="p")
    route = build_offline_context(None, kwargs={"pk": post.pk})
    spec = ServiceSpec(service=service, instance_selector_spec=_BY_PK)
    on_route = {"argument_binding": _SPREAD, "request": route.request, "view": route.view}

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={"title": "t"}, **on_route)
    assert excinfo.value.detail == {"non_field_errors": ["Missing required argument(s): 'pk'."]}

    assert dispatch(spec, params={"title": "t", "pk": post.pk}, **on_route).value == post.pk


class _TitleAndPkInput(serializers.Serializer):
    title = serializers.CharField()
    pk = serializers.IntegerField(required=False)


@_CORES
def test_a_field_named_like_a_lookup_key_is_still_named(dispatch: Any) -> None:
    """The lookup's keys reach a service beside an input serializer only through a
    field of that name, which the serializer validates into the spread. So a field
    ``pk`` the caller left out is named, and the resend carrying it is delivered."""
    post = Post.objects.create(title="p")
    route = build_offline_context(None, kwargs={"pk": post.pk})
    spec = ServiceSpec(
        service=_retitle_with_pk, input_serializer=_TitleAndPkInput, instance_selector_spec=_BY_PK
    )
    on_route = {"argument_binding": _SPREAD, "request": route.request, "view": route.view}

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={"title": "t"}, **on_route)
    assert excinfo.value.detail == {"non_field_errors": ["Missing required argument(s): 'pk'."]}

    assert dispatch(spec, params={"title": "t", "pk": post.pk}, **on_route).value == post.pk


class _AliasedTitleInput(serializers.Serializer):
    title = serializers.CharField(source="headline", required=False)


def _retitle_by_field_name(*, data: Any, title: str) -> str:
    return title


@_CORES
def test_a_source_aliased_field_is_asked_for_under_its_own_name(dispatch: Any) -> None:
    """The documented edge: a field with ``source=`` is declared under its own name
    but validated into the spread under its source, so a service naming the field
    is asked for ``title``, and the resend carrying it still leaves ``title``
    unfilled. The docs tell an author to name the source instead."""
    spec = ServiceSpec(service=_retitle_by_field_name, input_serializer=_AliasedTitleInput)

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={}, argument_binding=_SPREAD)
    assert excinfo.value.detail == {"non_field_errors": ["Missing required argument(s): 'title'."]}

    with pytest.raises(TypeError, match="'title'"):
        dispatch(spec, params={"title": "t"}, argument_binding=_SPREAD)


@_CORES
@pytest.mark.parametrize(
    ("spec", "params", "binding", "policy"),
    [
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_defaulted_tenant, preconditions=[_region_gate]
            ),
            {"pk": 1},
            ArgumentBinding.AUTO,
            UnknownArguments.IGNORE,
            id="selector-under-ignore",
        ),
        pytest.param(
            ServiceSpec(service=_close_for_reason, preconditions=[_region_gate]),
            {"reason": "r"},
            _SPREAD,
            UnknownArguments.PASSTHROUGH,
            id="spread-service-under-passthrough",
        ),
    ],
)
def test_an_undeclared_precondition_parameter_is_not_named_though_a_resend_would_reach_it(
    dispatch: Any,
    spec: Any,
    params: dict[str, Any],
    binding: ArgumentBinding,
    policy: UnknownArguments,
) -> None:
    """The fillable rule reads declared input, not what a permissive policy happens
    to deliver: ``region`` is not named, though the resend carrying it reaches the
    precondition, because under ``REJECT`` that resend would be refused as
    unexpected. An author who wants it named declares it."""
    with pytest.raises(TypeError, match="region"):
        dispatch(spec, params=params, argument_binding=binding, unknown_arguments=policy)

    dispatch(
        spec, params={**params, "region": "eu"}, argument_binding=binding, unknown_arguments=policy
    )
    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params={**params, "region": "eu"},
            argument_binding=binding,
            unknown_arguments=UnknownArguments.REJECT,
        )
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'region'."]}


if TYPE_CHECKING:
    # A ``TypedDict`` only the type checker sees, so the runtime cannot resolve the
    # ``**extras`` annotation naming it.
    class _CheckerOnlyExtras(TypedDict, total=False):
        region: str


def _tenant_rows(*, tenant: str, **extras: Unpack[_CheckerOnlyExtras]) -> list[str]:
    return [tenant]


@_CORES
@pytest.mark.parametrize(
    "policy",
    [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH],
    ids=["ignore", "passthrough"],
)
def test_an_unresolvable_extras_annotation_reads_as_open(
    dispatch: Any, policy: UnknownArguments
) -> None:
    """Nothing says which keys an unresolvable ``**extras`` takes, and the permissive
    policies deliver what the caller sends, so every name is one the caller could
    send: ``tenant`` is named, and the resend carrying it is delivered. ``REJECT``
    refuses to run on such a selector before anything is filled."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_tenant_rows)

    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={}, unknown_arguments=policy)
    assert excinfo.value.detail == _TENANT_MISSING
    assert dispatch(spec, params={"tenant": "acme"}, unknown_arguments=policy).value == ["acme"]
