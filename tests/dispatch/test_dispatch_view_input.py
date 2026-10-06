"""A caller's ``view`` is not input: it never reaches a callable and is never asked for.

No pool carries ``view``. A transport hands the calling view to a provider, and a
selector or service that wants it takes it from one (``kwargs=``, a
``get_*_kwargs`` hook) or from a route capture of that name. The input schema
hides it from a selector's parameters as it hides ``request`` and ``user``, but it
is not a reserved seed, so nothing in the pool outranked a caller's value: a
caller could send ``{"view": ...}`` and the callable received it, and a callable
requiring one was refused with ``view`` named as a missing argument, which a caller
could only satisfy by inventing one.

So dispatch treats ``view`` as every callable marking it ``NotClientInput``: the
caller's value is dropped at every site that spreads caller input, ``REJECT``
refuses it as unknown, and a required ``view`` nothing filled is never named
missing, but left to fail as the author's error it is.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core builds its pools on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
    OfflineServiceView,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_SPREADS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)
_SPOOFED = {"pk": 1, "view": "spoofed"}


def _select(*, pk: int, view: Any = None) -> dict[str, Any]:
    return {"pk": pk, "view": view}


_SELECT = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select)


@_CORES
@_SPREADS
def test_a_callers_view_never_reaches_a_selector(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    result = dispatch(_SELECT, params=_SPOOFED, argument_binding=binding)

    assert result.value == {"pk": 1, "view": None}


def _hand_over_the_view(view: Any) -> dict[str, Any]:
    return {"view": view}


@_CORES
@_SPREADS
def test_the_providers_view_is_what_a_selector_receives(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    """``SPREAD_CALLER_WINS`` ranks the caller's input over the provider, so only
    dropping the caller's ``view`` keeps the provider's."""
    view = OfflineServiceView(request=None)
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select, kwargs=_hand_over_the_view)

    result = dispatch(spec, params=_SPOOFED, view=view, argument_binding=binding)

    assert result.value == {"pk": 1, "view": view}


@_CORES
def test_a_route_capture_named_view_still_fills_it(dispatch: Dispatch) -> None:
    # Only the caller's mapping loses it: a route is the author's.
    view = OfflineServiceView(request=None, kwargs={"view": "route"})

    result = dispatch(_SELECT, params=_SPOOFED, view=view)

    assert result.value == {"pk": 1, "view": "route"}


@_CORES
def test_a_callers_view_is_unknown_under_reject(dispatch: Dispatch) -> None:
    # Declared by the selector, but never the caller's to send.
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_SELECT, params=_SPOOFED, unknown_arguments=UnknownArguments.REJECT)

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'view'."]}


@_CORES
def test_a_required_view_is_never_named_missing(dispatch: Dispatch) -> None:
    def select(*, pk: int, tenant: str, view: Any) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(ServiceValidationError) as excinfo:
        dispatch(spec, params={"pk": 1})

    assert excinfo.value.detail == {"non_field_errors": ["Missing required argument(s): 'tenant'."]}


@_CORES
def test_a_required_view_nothing_filled_is_the_authors_error(dispatch: Dispatch) -> None:
    """The caller's ``view`` is dropped, and nothing else fills it, so the call fails
    as it did before any refusal existed: no value the caller could send would
    help."""

    def select(*, pk: int, view: Any) -> None: ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)
    with pytest.raises(TypeError, match="view"):
        dispatch(spec, params=_SPOOFED)


# --- the other sites that spread caller input ---------------------------------------

_SEEN: list[Any] = []


def _post_by_pk(*, pk: int, view: Any = None) -> QuerySet[Post]:
    _SEEN.append(view)
    return Post.objects.filter(pk=pk)


def _close(*, instance: Post, reason: str, view: Any = None) -> dict[str, Any]:
    return {"reason": reason, "view": view}


def _close_open(*, instance: Post, **changes: Any) -> dict[str, Any]:
    return {key: changes[key] for key in ("reason", "view", "data") if key in changes}


@pytest.fixture
def seen() -> list[Any]:
    _SEEN.clear()
    return _SEEN


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    "policy",
    [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH],
    ids=["ignore", "passthrough"],
)
def test_a_callers_view_reaches_neither_a_lookup_nor_a_spread_service(
    dispatch: Dispatch, policy: UnknownArguments, seen: list[Any]
) -> None:
    post = Post.objects.create(title="p")
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)
    spec = ServiceSpec(service=_close, instance_selector_spec=lookup, atomic=False)

    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "view": "spoofed"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"reason": "dup", "view": None}
    assert seen == [None]


@pytest.mark.django_db(transaction=True)
@_CORES
@pytest.mark.parametrize(
    "policy",
    [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH],
    ids=["ignore", "passthrough"],
)
def test_a_callers_view_reaches_no_bare_var_keyword(
    dispatch: Dispatch, policy: UnknownArguments
) -> None:
    """An open surface takes every key the caller sent, and still not this one:
    not as a keyword, and not inside ``data``."""
    post = Post.objects.create(title="p")
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)
    spec = ServiceSpec(service=_close_open, instance_selector_spec=lookup, atomic=False)

    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "view": "spoofed"},
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=policy,
    )

    assert result.value == {"reason": "dup", "data": {"reason": "dup"}}
