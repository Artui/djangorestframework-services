"""A service spec with no ``input_serializer`` takes no client value for its own parameters.

``ArgumentBinding.AUTO`` resolves to ``BUNDLE`` for a service, as it always did,
and ``BUNDLE`` hands a client's arguments to a service only through what reads
them: the Validator, the target lookup and the spec's declared ``reads``. A
serializer-less service's own parameters are filled by the author - a view
hook, the spec's ``kwargs`` provider, a default - and never by the request, so
``DELETE {"hard": true}`` cannot set ``hard=True`` and a create cannot set
``is_admin``. Only an explicit ``SPREAD_AUTHOR_WINS`` takes them from the caller.
Each test runs over HTTP or through both cores, against the public signatures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    UnknownArguments,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Post

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


_LOOKUP = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk)


def _recording(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, instance: Any = None, is_admin: bool = False, data: Any = None) -> None:
        seen["is_admin"] = is_admin

    return service


@pytest.mark.django_db
def test_a_create_body_cannot_set_a_service_parameter_over_http() -> None:
    seen: dict[str, Any] = {}

    class _View(ServiceCreateView):
        spec = ServiceSpec(service=_recording(seen), atomic=False)

    response = _View.as_view()(_factory.post("/", {"is_admin": True}, format="json"))

    assert response.status_code in (201, 204)
    assert seen["is_admin"] is False


@pytest.mark.django_db
def test_an_update_body_cannot_set_a_service_parameter_over_http() -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="p")

    class _View(ServiceUpdateView):
        queryset = Post.objects.all()
        spec = ServiceSpec(service=_recording(seen), atomic=False)

    response = _View.as_view()(_factory.put("/", {"is_admin": True}, format="json"), pk=post.pk)

    assert response.status_code in (200, 204)
    assert seen["is_admin"] is False


@pytest.mark.django_db
def test_a_delete_body_cannot_set_a_service_parameter_over_http() -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="p")

    def destroy(*, instance: Post, hard: bool = False) -> None:
        seen["hard"] = hard

    class _View(ServiceDeleteView):
        queryset = Post.objects.all()
        spec = ServiceSpec(service=destroy, instance_selector_spec=_LOOKUP, atomic=False)

    response = _View.as_view()(_factory.delete("/", {"hard": True}, format="json"), pk=post.pk)

    assert response.status_code == 204
    assert seen["hard"] is False


@pytest.mark.django_db
@_CORES
def test_a_client_value_never_fills_a_service_parameter_off_http(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_recording(seen), atomic=False)

    dispatch(spec, params={"is_admin": True})

    assert seen["is_admin"] is False


@pytest.mark.django_db
@_CORES
def test_a_client_value_never_fills_one_beside_a_target_lookup(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    post = Post.objects.create(title="p")
    spec = ServiceSpec(service=_recording(seen), instance_selector_spec=_LOOKUP, atomic=False)

    dispatch(spec, params={"pk": post.pk, "is_admin": True})

    assert seen["is_admin"] is False


@pytest.mark.django_db
@_CORES
def test_reject_refuses_a_service_parameter_the_client_sends(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_recording({}), atomic=False)

    with pytest.raises(ValidationError) as caught:
        dispatch(spec, params={"is_admin": True}, unknown_arguments=UnknownArguments.REJECT)

    assert caught.value.detail == {"non_field_errors": ["Unexpected argument(s): 'is_admin'."]}


@pytest.mark.django_db
@_CORES
def test_a_required_service_parameter_is_the_authors_to_fill(dispatch: Dispatch) -> None:
    # Nothing the caller sends fills it, so leaving it unfilled is the author's
    # error, raised as the callable's own, never as a refusal asking the caller.
    def close(*, reason: str) -> None: ...

    with pytest.raises(TypeError, match="reason"):
        dispatch(ServiceSpec(service=close, atomic=False), params={"reason": "dup"})


@pytest.mark.django_db
@_CORES
def test_an_explicit_spreading_binding_still_takes_the_clients_value(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_recording(seen), atomic=False)

    dispatch(spec, params={"is_admin": True}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS)

    assert seen["is_admin"] is True


@pytest.mark.django_db
@_CORES
def test_one_spec_answers_each_binding_as_its_own(dispatch: Dispatch) -> None:
    # The compiled variant is kept per spec object, so a spreading call must not
    # leave the next bundled one spreading, nor the reverse.
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_recording(seen), atomic=False)
    answers = []
    for binding in (
        ArgumentBinding.AUTO,
        ArgumentBinding.SPREAD_AUTHOR_WINS,
        ArgumentBinding.BUNDLE,
    ):
        dispatch(spec, params={"is_admin": True}, argument_binding=binding)
        answers.append(seen["is_admin"])

    assert answers == [False, True, False]


@pytest.mark.django_db
@_CORES
def test_an_authors_value_still_fills_a_service_parameter(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_recording(seen), atomic=False)

    dispatch(
        spec,
        params={"is_admin": False},
        view_hooks=ViewHooks(extra_kwargs={"is_admin": True}),
    )

    assert seen["is_admin"] is True


@pytest.mark.django_db
@_CORES
def test_a_declared_read_still_reaches_the_service(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def service(*, org: str = "none", is_admin: bool = False) -> None:
        seen["org"] = org
        seen["is_admin"] = is_admin

    spec = ServiceSpec(service=service, reads=[UrlKwarg("org")], atomic=False)

    dispatch(spec, params={"org": "acme", "is_admin": True})

    assert seen == {"org": "acme", "is_admin": False}


def test_the_compiled_spec_declares_no_service_parameter() -> None:
    # What every kernel entry point reads - its dispatch, the registry, the
    # schemas - so the parameter is closed there too, not only at this edge.
    spec = ServiceSpec(service=_recording({}), instance_selector_spec=_LOOKUP, atomic=False)

    assert set(kernel_spec(spec).parameters().names()) == {"pk"}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS])
def test_a_selectors_reads_are_the_callers_under_every_binding(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    """The binding decides only whether a service's own parameters are caller input."""
    post = Post.objects.create(title="p")
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk)

    result = dispatch(spec, params={"pk": post.pk}, argument_binding=binding)

    assert result.value == post
