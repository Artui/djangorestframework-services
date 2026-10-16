"""A call missing several required arguments is refused one stage at a time.

Every refusal keys each name it reports, ``{"name": ["This field is
required."]}`` with code ``required``, and reports every name missing at its
stage. The stages are the call's own order, so a call missing names of more
than one kind is answered in up to three refusals rather than one:

1. A name the declaration requires: one marked ``InputRequired``, a required
   declared read, an input serializer's required field. The shape check
   refuses it before the permission checks run.
2. A parameter of the service or selector with no default. A provider, a
   view hook or a pool seed may still fill it, so it is asked for only once
   they have run.
3. Under ``SPREAD_AUTHOR_WINS``, a service naming its target lookup's key, which
   is asked for as the service is called.

Each test sends what the earlier stage asked for and asserts what the next one
asks, so it names the stage it holds.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ArgumentBinding,
    InputRequired,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)
from rest_framework_services.dispatch import build_offline_context
from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _refused(dispatch: Any, spec: Any, **kwargs: Any) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, **kwargs)
    codes: Any = excinfo.value.get_codes()
    return codes


def select(*, zeta: int, marked: Annotated[int, InputRequired], alpha: int, pk: int) -> Any:
    return "ran"


_SELECT = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=select)


@_CORES
def test_a_marked_name_is_asked_for_before_the_selector_s_own(dispatch: Any) -> None:
    assert _refused(dispatch, _SELECT, params={"pk": 1}) == {"marked": ["required"]}


@_CORES
def test_then_every_parameter_with_no_default_is_asked_for_at_once(dispatch: Any) -> None:
    assert _refused(dispatch, _SELECT, params={"pk": 1, "marked": 1}) == {
        "alpha": ["required"],
        "zeta": ["required"],
    }


@_CORES
def test_with_every_name_sent_the_selector_runs(dispatch: Any) -> None:
    result = dispatch(_SELECT, params={"pk": 1, "marked": 1, "alpha": 1, "zeta": 1})
    assert result.value == "ran"


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def retitle(
    *, instance: Post, title: Annotated[str, InputRequired], note: str, pk: Any
) -> dict[str, Any]:
    return {"title": title, "note": note, "pk": pk}


_RETITLE = ServiceSpec(
    service=retitle,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
)


def _spread(post: Post, params: dict[str, Any]) -> dict[str, Any]:
    context = build_offline_context(None, kwargs={"pk": post.pk})
    return {
        "params": params,
        "argument_binding": ArgumentBinding.SPREAD_AUTHOR_WINS,
        "request": context.request,
        "view": context.view,
    }


@_CORES
@pytest.mark.parametrize(
    ("sent", "asked"),
    [
        ({}, {"title": ["required"]}),
        ({"title": "t"}, {"note": ["required"]}),
        ({"title": "t", "note": "n"}, {"pk": ["required"]}),
    ],
    ids=["marked", "own", "lookup-key"],
)
def test_a_spread_service_asks_for_its_lookup_s_key_last(
    dispatch: Any, sent: dict[str, Any], asked: dict[str, list[str]]
) -> None:
    post = Post.objects.create(title="p")
    assert _refused(dispatch, _RETITLE, **_spread(post, sent)) == asked
