"""A spread service naming its target lookup's key takes the caller's value for it, as on 0.57.0.

Under ``SPREAD_AUTHOR_WINS`` a service's signature is the caller's input. The
target lookup consumes the caller's ``pk`` to find the row, and a service
that names ``pk`` as well asks for it by name, so it receives the caller's own
value, the one the client sent rather than the route's capture the lookup
used, and below an author's value as everywhere else. A precondition naming
it is handed it alike. One that leaves it out, where the callable has no
default for it, is the caller's ``400``: the client can send it. It is
answered as that callable is called, so a refusal the row's lookup or an
earlier precondition gives comes first. A service with an input serializer
takes the key through its field instead, and is refused alike when the
validated input has none. Neither ``AUTO`` nor ``BUNDLE`` makes a service's
parameters the caller's, so there the parameter is the author's to fill.
"""

from __future__ import annotations

import functools
from typing import Annotated, Any, TypedDict

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from typing_extensions import Unpack

from rest_framework_services import (
    ArgumentBinding,
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    ServiceValidationError,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

SPREAD = ArgumentBinding.SPREAD_AUTHOR_WINS
_factory = APIRequestFactory()
_REQUIRED = {"pk": ["This field is required."]}


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _routed(pk: Any) -> dict[str, Any]:
    context = build_offline_context(None, kwargs={"pk": pk})
    return {"request": context.request, "view": context.view}


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


_BY_PK = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)


def retitle_naming_pk(*, instance: Post, title: str, pk: Any) -> dict[str, Any]:
    return {"pk": pk, "row": instance.pk}


async def aretitle_naming_pk(*, instance: Post, title: str, pk: Any) -> dict[str, Any]:
    return {"pk": pk, "row": instance.pk}


_SERVICES = pytest.mark.parametrize(
    "service", [retitle_naming_pk, aretitle_naming_pk], ids=["def", "async-def"]
)


def _spec(service: Any = retitle_naming_pk, **kwargs: Any) -> Any:
    return ServiceSpec(service=service, instance_selector_spec=_BY_PK, **kwargs)


@_CORES
@_SERVICES
def test_the_service_takes_the_caller_s_value_where_the_route_scopes_the_lookup(
    dispatch: Any, service: Any
) -> None:
    post = Post.objects.create(title="p")
    result = dispatch(
        _spec(service),
        params={"title": "t", "pk": "999"},
        argument_binding=SPREAD,
        **_routed(post.pk),
    )
    assert result.value == {"pk": "999", "row": post.pk}


@_CORES
def test_with_no_route_the_caller_s_value_scopes_the_lookup_and_reaches_the_service(
    dispatch: Any,
) -> None:
    post = Post.objects.create(title="p")
    result = dispatch(_spec(), params={"title": "t", "pk": post.pk}, argument_binding=SPREAD)
    assert result.value == {"pk": post.pk, "row": post.pk}


@_CORES
@_SERVICES
def test_a_service_naming_the_lookup_s_key_the_caller_left_out_is_refused(
    dispatch: Any, service: Any
) -> None:
    post = Post.objects.create(title="p")
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_spec(service), params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert excinfo.value.detail == _REQUIRED
    assert excinfo.value.get_codes() == {"pk": ["required"]}


@_CORES
def test_a_handed_row_still_asks_the_caller_for_the_key(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_spec(), params={"title": "t"}, instance=post, argument_binding=SPREAD)
    assert excinfo.value.detail == _REQUIRED


@_CORES
def test_a_missing_row_is_not_found_before_the_key_is_asked_for(dispatch: Any) -> None:
    result = dispatch(_spec(), params={"title": "t"}, argument_binding=SPREAD, **_routed(99999))
    assert result.kind == "not_found"


def retitle_default_pk(*, instance: Post, title: str, pk: Any = "default") -> Any:
    return pk


@_CORES
def test_a_parameter_with_a_default_takes_the_caller_s_value_or_keeps_its_own(
    dispatch: Any,
) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(retitle_default_pk)
    sent = dispatch(
        spec, params={"title": "t", "pk": "999"}, argument_binding=SPREAD, **_routed(post.pk)
    )
    left = dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert (sent.value, left.value) == ("999", "default")


def open_naming_pk(*, instance: Post, pk: Any, **changes: Any) -> Any:
    return {"pk": pk, "pk-in-changes": "pk" in changes}


@_CORES
def test_an_open_service_naming_the_key_takes_it_by_name_alone(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(open_naming_pk)
    result = dispatch(
        spec, params={"title": "t", "pk": "999"}, argument_binding=SPREAD, **_routed(post.pk)
    )
    assert result.value == {"pk": "999", "pk-in-changes": False}
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert excinfo.value.detail == _REQUIRED


def _gate_naming_pk(*, pk: Any) -> None:
    if pk != "999":
        raise ServiceValidationError(f"gate saw {pk!r}")


@_CORES
def test_a_precondition_naming_the_key_beside_the_service_takes_the_caller_s_value(
    dispatch: Any,
) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(retitle_default_pk, preconditions=[_gate_naming_pk])
    result = dispatch(
        spec, params={"title": "t", "pk": "999"}, argument_binding=SPREAD, **_routed(post.pk)
    )
    assert result.value == "999"
    # The service's own default answers for it, and the precondition has none.
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert excinfo.value.detail == _REQUIRED


@_CORES
def test_a_precondition_naming_a_key_the_service_does_not_is_the_author_s_error(
    dispatch: Any,
) -> None:
    """The lookup's read reaches the call because the service asks for it by
    name, so with a service that does not, nothing hands it to the precondition."""
    post = Post.objects.create(title="p")
    spec = _spec(lambda *, instance, title: "ran", preconditions=[_gate_naming_pk])
    for params in ({"title": "t"}, {"title": "t", "pk": "999"}):
        with pytest.raises(TypeError, match="_gate_naming_pk"):
            dispatch(spec, params=params, argument_binding=SPREAD, **_routed(post.pk))


def _refusing_gate(*, instance: Post) -> None:
    raise ServiceValidationError("the gate refused")


@_CORES
def test_an_earlier_precondition_s_refusal_answers_before_the_key_is_asked_for(
    dispatch: Any,
) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(preconditions=[_refusing_gate])
    with pytest.raises(ServiceValidationError, match="the gate refused"):
        dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))


@_CORES
def test_an_author_s_value_outranks_the_caller_s(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(kwargs=lambda: {"pk": post.pk})
    result = dispatch(
        spec, params={"title": "t", "pk": "999"}, argument_binding=SPREAD, **_routed(post.pk)
    )
    assert result.value == {"pk": post.pk, "row": post.pk}


def _injecting(fn: Any) -> Any:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("pk", "injected")
        return fn(*args, **kwargs)

    return wrapper


@_CORES
def test_a_wrapper_filling_the_key_itself_runs(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(_injecting(retitle_naming_pk))
    result = dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert result.value == {"pk": "injected", "row": post.pk}


@_CORES
@pytest.mark.parametrize("binding", [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE])
def test_without_a_spreading_binding_the_key_is_the_author_s_to_fill(
    dispatch: Any, binding: ArgumentBinding
) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(lambda *, instance, pk: pk)
    for params in ({}, {"pk": "999"}):
        with pytest.raises(TypeError, match="pk"):
            dispatch(spec, params=params, argument_binding=binding, **_routed(post.pk))


class _TitleAndPk(serializers.Serializer):
    title = serializers.CharField(required=False)
    pk = serializers.IntegerField(required=False)


class _PkWithDefault(serializers.Serializer):
    pk = serializers.IntegerField(default=5)


def retitle_with_pk(*, instance: Post, data: Any, pk: Any) -> Any:
    return pk


@_CORES
def test_a_serializer_s_field_delivers_the_key_and_its_absence_is_refused(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(retitle_with_pk, input_serializer=_TitleAndPk)
    # The field's value, as validated: never the string the client sent.
    sent = dispatch(spec, params={"pk": "999"}, argument_binding=SPREAD, **_routed(post.pk))
    assert sent.value == 999
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"title": "t"}, argument_binding=SPREAD, **_routed(post.pk))
    assert excinfo.value.detail == _REQUIRED


@_CORES
def test_a_serializer_field_s_default_fills_the_key(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = _spec(retitle_with_pk, input_serializer=_PkWithDefault)
    result = dispatch(spec, params={}, argument_binding=SPREAD, **_routed(post.pk))
    assert result.value == 5


def _live(post: Post, body: dict[str, Any]) -> ServiceUpdateView:
    """A live view as DRF builds one per request, for a transport to dispatch."""
    view = ServiceUpdateView()
    view.request = Request(_factory.put("/", body, format="json"), parsers=[JSONParser()])
    view.request.user = AnonymousUser()
    view.kwargs = {"pk": post.pk}
    view.format_kwarg = None
    return view


@_CORES
def test_over_a_live_view_the_service_takes_the_body_s_value_and_asks_for_a_missing_one(
    dispatch: Any,
) -> None:
    post = Post.objects.create(title="p")
    view = _live(post, {"title": "t", "pk": 999})
    result = dispatch(
        _spec(), params=view.request.data, request=view.request, view=view, argument_binding=SPREAD
    )
    assert result.value == {"pk": 999, "row": post.pk}
    view = _live(post, {"title": "t"})
    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            _spec(),
            params=view.request.data,
            request=view.request,
            view=view,
            argument_binding=SPREAD,
        )
    assert excinfo.value.detail == _REQUIRED


@_CORES
def test_arguments_that_are_no_object_send_no_lookup_key(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    # A list holding the key's name, which ``in`` finds and no index reads.
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_spec(), params=["pk"], argument_binding=SPREAD, **_routed(post.pk))
    assert excinfo.value.get_codes() == {"non_field_errors": ["invalid"]}


def _positional_pk(pk: Any, /, *, instance: Post, title: str) -> Any:
    return pk


@_CORES
def test_a_positional_only_parameter_is_never_asked_for(dispatch: Any) -> None:
    """Dispatch passes every argument by name, so no value the client sends
    could fill it: the call is the author's ``TypeError``, never a ``400``, and
    a value sent is never passed to it by name."""
    post = Post.objects.create(title="p")
    for params in ({"title": "t"}, {"title": "t", "pk": "999"}):
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'pk'"):
            dispatch(
                _spec(_positional_pk), params=params, argument_binding=SPREAD, **_routed(post.pk)
            )


def retitle_scoped(*, instance: Post, data: Any, pk: Any, tenant: str) -> Any:
    return pk


@_CORES
def test_a_parameter_no_caller_can_fill_stays_the_author_s_error(dispatch: Any) -> None:
    """``tenant`` is no field of the serializer and no read of the lookup."""
    post = Post.objects.create(title="p")
    spec = _spec(retitle_scoped, input_serializer=_TitleAndPk)
    with pytest.raises(TypeError, match="tenant"):
        dispatch(spec, params={"pk": 1}, argument_binding=SPREAD, **_routed(post.pk))


class _TitleOnly(serializers.Serializer):
    title = serializers.CharField(required=False)


@_CORES
def test_a_serializer_with_no_field_for_the_key_leaves_it_to_the_author(dispatch: Any) -> None:
    """No resend could fill it: nothing delivers it beside a serializer but a field."""
    post = Post.objects.create(title="p")
    spec = _spec(retitle_with_pk, input_serializer=_TitleOnly)
    for params in ({"title": "t"}, {"title": "t", "pk": "999"}):
        with pytest.raises(TypeError, match="pk"):
            dispatch(spec, params=params, argument_binding=SPREAD, **_routed(post.pk))


class _MaybePk(TypedDict, total=False):
    pk: Any
    title: str


class _MustPk(TypedDict):
    pk: Any


def _unpacking(*, instance: Post, **changes: Unpack[_MaybePk]) -> Any:
    return changes.get("pk", "absent")


def _unpacking_required(*, instance: Post, **changes: Unpack[_MustPk]) -> Any:
    return changes.get("pk", "absent")


@_CORES
def test_a_typed_dict_key_takes_the_caller_s_value_and_is_never_asked_for(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    sent = dispatch(
        _spec(_unpacking),
        params={"title": "t", "pk": "999"},
        argument_binding=SPREAD,
        **_routed(post.pk),
    )
    left = dispatch(
        _spec(_unpacking_required), params={}, argument_binding=SPREAD, **_routed(post.pk)
    )
    assert (sent.value, left.value) == ("999", "absent")


def _post_for_tenant(*, pk: int, tenant: Annotated[str, NotClientInput]) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _names_tenant(*, instance: Post, tenant: Any) -> Any:
    return tenant


@_CORES
def test_a_key_the_lookup_hides_never_takes_the_caller_s_value(dispatch: Any) -> None:
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_names_tenant,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_for_tenant),
    )
    context = build_offline_context(None, kwargs={"pk": post.pk, "tenant": "acme"})
    result = dispatch(
        spec,
        params={"tenant": "evil"},
        request=context.request,
        view=context.view,
        argument_binding=SPREAD,
    )
    assert result.value == "acme"
