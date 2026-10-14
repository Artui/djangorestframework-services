"""A caller's ``view`` is not input: the edge seeds the calling view itself.

``dispatch_spec`` and ``adispatch_spec`` register ``view`` as a pool seed for every
call, beside ``request``: the view they were handed, or ``None`` off HTTP. So a
callable declaring ``view`` receives the transport's, a caller's ``{"view": ...}``
never reaches it (``REJECT`` refuses it as unknown), a required ``view`` is never
missing, and a provider returning one is refused for outranking a seed, as the
kernel refuses every provider that does.

Each test runs through ``dispatch_spec`` and ``adispatch_spec`` alike, because each
core builds its pools on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
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
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_BINDINGS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["auto", "author-wins"],
)
_SPOOFED = {"pk": 1, "view": "spoofed"}


def _select(*, pk: int, view: Any = None) -> dict[str, Any]:
    return {"pk": pk, "view": view}


_SELECT = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select)


@_CORES
@_BINDINGS
def test_a_callers_view_never_reaches_a_selector(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    result = dispatch(_SELECT, params=_SPOOFED, argument_binding=binding)

    assert result.value == {"pk": 1, "view": None}


def _hand_over_the_view(view: Any) -> dict[str, Any]:
    return {"view": view}


@_CORES
@_BINDINGS
def test_the_transports_view_is_what_a_selector_receives(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    view = OfflineServiceView(request=None)

    result = dispatch(_SELECT, params=_SPOOFED, view=view, argument_binding=binding)

    assert result.value == {"pk": 1, "view": view}


@_CORES
def test_a_provider_handing_over_the_view_is_refused(dispatch: Dispatch) -> None:
    """The seed already fills it, and a provider never outranks a seed."""
    view = OfflineServiceView(request=None)
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select, kwargs=_hand_over_the_view)

    with pytest.raises(ImproperlyConfigured, match=r"\['view'\], which dispatch seeds itself"):
        dispatch(spec, params={"pk": 1}, view=view)


@_CORES
def test_a_route_capture_named_view_loses_to_the_seed(dispatch: Dispatch) -> None:
    view = OfflineServiceView(request=None, kwargs={"view": "route"})

    result = dispatch(_SELECT, params=_SPOOFED, view=view)

    assert result.value == {"pk": 1, "view": view}


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
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"pk": 1})

    assert excinfo.value.detail == {"tenant": ["This field is required."]}


@_CORES
def test_a_required_view_the_caller_did_not_hand_over_is_none(dispatch: Dispatch) -> None:
    """Off HTTP the seed is ``None``, so a callable requiring ``view`` still runs."""

    def select(*, pk: int, view: Any) -> dict[str, Any]:
        return {"pk": pk, "view": view}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)

    assert dispatch(spec, params=_SPOOFED).value == {"pk": 1, "view": None}


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
def test_a_callers_view_reaches_neither_a_lookup_nor_a_spread_service(
    dispatch: Dispatch, seen: list[Any]
) -> None:
    post = Post.objects.create(title="p")
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)
    spec = ServiceSpec(service=_close, instance_selector_spec=lookup, atomic=False)

    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "view": "spoofed"},
        unknown_arguments=UnknownArguments.IGNORE,
    )

    assert result.value == {"reason": "dup", "view": None}
    assert seen == [None]


@pytest.mark.django_db(transaction=True)
@_CORES
def test_a_bare_var_keyword_gets_the_seed_and_no_undeclared_argument(
    dispatch: Dispatch,
) -> None:
    """An open surface is in the seeds' pool, so it receives the seeded ``view``;
    what the caller sent beside the lookup's ``pk`` is declared nowhere, so neither
    ``reason`` nor the caller's ``view`` reaches it, as a keyword or inside ``data``."""
    post = Post.objects.create(title="p")
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk)
    spec = ServiceSpec(service=_close_open, instance_selector_spec=lookup, atomic=False)

    result = dispatch(
        spec,
        params={"pk": post.pk, "reason": "dup", "view": "spoofed"},
        unknown_arguments=UnknownArguments.IGNORE,
    )

    assert result.value == {"view": None, "data": {}}
