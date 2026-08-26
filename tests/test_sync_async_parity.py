"""Sync/async parity: one case, both dispatch cores, one outcome.

``tests/test_transport_parity.py`` guards a different axis — the HTTP view path
against the transport-neutral core. This module guards the axis the *pair* of
cores form between them: every test here drives the same case through
``dispatch_spec`` and ``adispatch_spec`` (or through a render twin and its async
sibling) and asserts the two agree. A fix applied to one core and not the other
fails here, rather than reaching an async transport as a silently different
answer.

The kernel the twins share, and are therefore most likely to drift on:

- **the hand-built pools in target resolution** — each core assembles its own
  pool for ``instance_selector_spec`` / ``collection_selector_spec``, and each
  has to strip the reserved seeds itself, because that pool decides *which row*
  gets mutated and off HTTP the params are the caller's tool call;
- **the mutation tail** — the ``DispatchResult`` fields the mutation path fills
  in, and the stale-prefetch clear that runs before the output re-fetch;
- **the signatures themselves** — a parameter added to one twin and not the
  other is drift that no behavioural test can see, because the async surface
  simply cannot be handed the argument.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.db.models import Model, QuerySet
from django.http import QueryDict
from rest_framework import serializers

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    arender_for_agent,
    arender_spec_output,
    build_offline_context,
    dispatch_spec,
    render_for_agent,
    render_spec_output,
)
from tests.testapp.models import Catalog, Post, Section

# --- the harness ---------------------------------------------------------


def _plain(value: Any) -> Any:
    """Reduce a resolved value to something two separate runs compare equal by.

    The two cores resolve two different Python objects for the same database
    row, so the comparison has to be by identity-in-the-database rather than by
    object. Everything that is not a model row or a container of them is
    already comparable and passes through.
    """
    if isinstance(value, QuerySet):
        return [_plain(item) for item in value]
    if isinstance(value, Model):
        return (type(value).__name__, value.pk)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _summarize(result: DispatchResult) -> dict[str, Any]:
    """Every field of the result contract, reduced to comparable form.

    All six, deliberately: a summary that only carried ``value`` / ``kind`` /
    ``status`` would agree across cores that disagreed about the mutation tail,
    which is exactly the drift this module exists to catch.
    """
    return {
        "kind": result.kind,
        "status": result.status,
        "value": _plain(result.value),
        "service_result": _plain(result.service_result),
        "instance": _plain(result.instance),
        "data": _plain(result.data),
    }


def _run_sync(spec: Any, make_kwargs: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The whole sync run in one thread hop, summarizing included.

    Summarizing evaluates a returned queryset, so it has to happen on this side
    of the hop rather than back on the event loop.
    """
    return _summarize(dispatch_spec(spec, **make_kwargs()))


async def _dispatch_both(
    spec: Any, make_kwargs: Callable[[], dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drive one case through both cores; return ``(sync, async)`` summaries.

    ``make_kwargs`` is called once per run rather than built once and shared,
    so a case whose fixture the mutation consumes can hand each core its own
    row. It runs off the loop, because building that fixture is ORM work.
    """
    sync_summary = await sync_to_async(_run_sync, thread_sensitive=True)(spec, make_kwargs)
    async_kwargs = await sync_to_async(make_kwargs, thread_sensitive=True)()
    async_result = await adispatch_spec(spec, **async_kwargs)
    async_summary = await sync_to_async(_summarize, thread_sensitive=True)(async_result)
    return sync_summary, async_summary


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()


def _echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


def _posts_by_pk(*, pk: Any) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


# --- the twins take the same arguments -----------------------------------
#
# Each async twin's docstring promises identical arguments, and each one is a
# separate definition that has to be kept in step by hand. This is the cheap
# structural check that they were: a keyword added to one and not the other is
# invisible to every behavioural test, since the drifted surface cannot be
# handed the argument at all — the caller finds out with a ``TypeError``.


def _parameters(fn: Any) -> list[tuple[str, Any]]:
    return [(name, param.default) for name, param in inspect.signature(fn).parameters.items()]


@pytest.mark.parametrize(
    ("sync_twin", "async_twin"),
    [
        (dispatch_spec, adispatch_spec),
        (render_spec_output, arender_spec_output),
        (render_for_agent, arender_for_agent),
    ],
    ids=["dispatch_spec", "render_spec_output", "render_for_agent"],
)
def test_an_async_twin_takes_the_same_arguments_as_its_sync_twin(
    sync_twin: Any, async_twin: Any
) -> None:
    assert _parameters(async_twin) == _parameters(sync_twin)


# --- reserved pool seeds in target resolution -----------------------------
#
# ``RESERVED_POOL_SEEDS`` exists because a client-routable value named after a
# dispatcher seed would override the dispatcher's authoritative one. Both cores
# hand-build the pool for the nested target selectors instead of going through
# ``merge_arguments``, so both have to strip the seeds themselves — and the two
# tests that pinned this were sync-only, so the async core's copy was unguarded
# by anything.


@pytest.mark.django_db(transaction=True)
async def test_params_cannot_shadow_the_user_in_target_resolution_on_either_core() -> None:
    """The pool that picks which *row* gets mutated, on both cores."""
    seen: list[Any] = []
    real = await User.objects.acreate(username="real")
    post = await Post.objects.acreate(title="p")

    def target(*, pk: Any, user: Any) -> QuerySet[Post]:
        seen.append(user)
        return Post.objects.filter(pk=pk)

    spec = ServiceSpec(
        service=lambda *, instance: None,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=target),
    )
    sync_summary, async_summary = await _dispatch_both(
        spec,
        lambda: {"user": real, "params": {"pk": post.pk, "user": "client-supplied"}},
    )
    assert sync_summary == async_summary
    assert seen == [real, real]


@pytest.mark.django_db(transaction=True)
async def test_params_cannot_shadow_the_user_in_collection_resolution_on_either_core() -> None:
    """The bulk twin: the pool that picks which *set* gets mutated."""
    seen: list[Any] = []
    real = await User.objects.acreate(username="real")

    def collection(*, user: Any) -> QuerySet[Post]:
        seen.append(user)
        return Post.objects.none()

    spec = ServiceSpec(
        service=lambda *, collection: None,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=collection),
    )
    sync_summary, async_summary = await _dispatch_both(
        spec, lambda: {"user": real, "params": {"user": "client-supplied"}}
    )
    assert sync_summary == async_summary
    assert seen == [real, real]


@pytest.mark.django_db(transaction=True)
async def test_route_captures_reach_the_target_pool_on_either_core() -> None:
    """The third ingredient of that pool: the offline view's route captures.

    They rank above ``params`` and below the spec's own ``kwargs`` provider,
    and they are seed-stripped too — so a capture named ``user`` loses to the
    acting one just as a param does.
    """
    seen: list[Any] = []
    real = await User.objects.acreate(username="real")
    post = await Post.objects.acreate(title="p")

    def target(*, pk: Any, user: Any, tenant: Any) -> QuerySet[Post]:
        seen.append((user, tenant))
        return Post.objects.filter(pk=pk)

    spec = ServiceSpec(
        service=lambda *, instance: None,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=target),
    )

    def make_kwargs() -> dict[str, Any]:
        context = build_offline_context(
            user=real, kwargs={"user": "route-supplied", "tenant": "from-route"}
        )
        return {
            "user": real,
            "params": {"pk": post.pk},
            "request": context.request,
            "view": context.view,
        }

    sync_summary, async_summary = await _dispatch_both(spec, make_kwargs)
    assert sync_summary == async_summary
    assert seen == [(real, "from-route"), (real, "from-route")]


# --- the mutation tail ----------------------------------------------------
#
# ``DispatchResult`` documents six fields, three of which only the mutation path
# fills in. A transport reading ``result.instance`` for its audit log, or
# ``result.data`` for what was validated, gets ``None`` from a core that skipped
# them — no exception, no warning, just a mutation recorded as having had no
# input and no target.


def _rename(*, instance: Post, data: Any) -> dict[str, Any]:
    instance.title = data["title"]
    instance.save()
    return {"renamed": True}


@pytest.mark.django_db(transaction=True)
async def test_the_mutation_tail_agrees_across_the_cores() -> None:
    post = await Post.objects.acreate(title="before")
    spec = ServiceSpec(
        service=_rename,
        input_serializer=_TitleInput,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_posts_by_pk),
    )
    sync_summary, async_summary = await _dispatch_both(
        spec, lambda: {"user": None, "params": {"pk": post.pk, "title": "after"}}
    )
    assert sync_summary == async_summary
    # Pinned by value as well as by agreement: three ``None``s would agree too.
    assert async_summary["service_result"] == {"renamed": True}
    assert async_summary["instance"] == ("Post", post.pk)
    assert async_summary["data"] == {"title": "after"}


# --- the mutated target's stale prefetch ----------------------------------
#
# A service that changed a prefetched relation leaves the target's
# ``_prefetched_objects_cache`` stale, so re-reading it serves pre-mutation
# rows. Clearing it mirrors DRF's ``UpdateModelMixin``. The relation here is
# changed by deleting the rows directly rather than through
# ``instance.sections`` — a related manager invalidates its own cache entry, so
# going through it would hide the very staleness under test.


def _drop_sections(*, instance: Catalog) -> Catalog:
    Section.objects.filter(catalog=instance).delete()
    return instance


_PREFETCH_SPEC = ServiceSpec(
    service=_drop_sections,
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Catalog.objects.filter(pk=pk),
        prefetch_related=("sections",),
    ),
)


def _catalog_with_a_section() -> Catalog:
    catalog = Catalog.objects.create(name="c")
    Section.objects.create(catalog=catalog, title="stale")
    return catalog


def _sections_seen_by_sync() -> list[str]:
    result = dispatch_spec(_PREFETCH_SPEC, user=None, params={"pk": _catalog_with_a_section().pk})
    return [section.title for section in result.instance.sections.all()]


@pytest.mark.django_db(transaction=True)
async def test_a_mutated_targets_stale_prefetch_is_dropped_by_either_core() -> None:
    sync_sections = await sync_to_async(_sections_seen_by_sync, thread_sensitive=True)()
    catalog = await sync_to_async(_catalog_with_a_section, thread_sensitive=True)()
    result = await adispatch_spec(_PREFETCH_SPEC, user=None, params={"pk": catalog.pk})
    async_sections = await sync_to_async(
        lambda: [section.title for section in result.instance.sections.all()],
        thread_sensitive=True,
    )()
    assert async_sections == sync_sections
    assert async_sections == []


# --- a caller-resolved target ---------------------------------------------
#
# ``instance=`` is how a caller that already fetched and authorised a row pins
# the mutation to it. Its default is a sentinel rather than ``None`` because
# ``None`` is a *supplied* value — a create — so both readings have to survive
# on both cores.


@pytest.mark.django_db(transaction=True)
async def test_a_caller_resolved_instance_pins_the_target_on_either_core() -> None:
    """The passed row wins, and ``instance_selector_spec`` never runs."""
    seen: list[Any] = []
    pinned = await Post.objects.acreate(title="pinned")
    await Post.objects.acreate(title="decoy")

    def decoy_target(**_: Any) -> QuerySet[Post]:
        seen.append("ran")
        return Post.objects.filter(title="decoy")

    spec = ServiceSpec(
        service=lambda *, instance: instance,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=decoy_target),
    )
    sync_summary, async_summary = await _dispatch_both(
        spec, lambda: {"user": None, "params": {}, "instance": pinned}
    )
    assert sync_summary == async_summary
    assert async_summary["instance"] == ("Post", pinned.pk)
    assert seen == []


@pytest.mark.django_db(transaction=True)
async def test_an_explicit_none_instance_is_a_create_on_either_core() -> None:
    """``None`` is supplied, not omitted — the sentinel default is load-bearing."""
    seen: list[Any] = []

    def target(**_: Any) -> QuerySet[Post]:
        seen.append("ran")
        return Post.objects.all()

    spec = ServiceSpec(
        service=lambda: "created",
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=target),
    )
    sync_summary, async_summary = await _dispatch_both(
        spec, lambda: {"user": None, "params": {}, "instance": None}
    )
    assert sync_summary == async_summary
    assert async_summary["value"] == "created"
    assert async_summary["instance"] is None
    assert seen == []


# --- a form-encoded body --------------------------------------------------
#
# A form-encoded / multipart body is a ``QueryDict``, whose internal storage is
# ``{key: [values]}``. ``dict()``-ing one exposes those value lists and turns
# every scalar into a one-element list, so a ``CharField`` answers "Not a valid
# string" — a 400 on input the other core validates. The hazard is invisible to
# JSON-only tests, which is why it gets its own case on this axis as well as on
# the HTTP one.


@pytest.mark.django_db(transaction=True)
async def test_a_form_encoded_body_validates_the_same_on_either_core() -> None:
    spec = ServiceSpec(service=_echo, input_serializer=_TitleInput)
    sync_summary, async_summary = await _dispatch_both(
        spec, lambda: {"user": None, "params": QueryDict("title=Alice")}
    )
    assert sync_summary == async_summary
    assert async_summary["value"] == {"title": "Alice"}


# --- the render twins -----------------------------------------------------
#
# ``view_hooks`` is how a view's ``get_output_serializer_context`` chain reaches
# the renderer. ``adispatch_spec`` accepts the carrier, so an async caller can
# hold one; the render twins have to accept it too, or that caller can dispatch
# with the chain and then not render with it.


class _TenantReadingSerializer(serializers.ModelSerializer):
    tenant = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ("id", "tenant")

    def get_tenant(self, _: Post) -> Any:
        return self.context.get("tenant")


_RENDER_SPEC = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=_posts_by_pk,
    output_serializer=_TenantReadingSerializer,
)
_HOOKS = ViewHooks(output_serializer_context=lambda _: {"tenant": "from-view-hook"})


@pytest.mark.django_db(transaction=True)
async def test_view_hooks_reach_the_output_context_on_either_render_twin() -> None:
    post = await Post.objects.acreate(title="p")
    sync_payload = await sync_to_async(render_spec_output, thread_sensitive=True)(
        _RENDER_SPEC, post, view_hooks=_HOOKS
    )
    async_payload = await arender_spec_output(_RENDER_SPEC, post, view_hooks=_HOOKS)
    assert async_payload == sync_payload
    assert async_payload["tenant"] == "from-view-hook"


@pytest.mark.django_db(transaction=True)
async def test_view_hooks_reach_the_output_context_on_either_agent_render_twin() -> None:
    post = await Post.objects.acreate(title="p")
    sync_payload = await sync_to_async(render_for_agent, thread_sensitive=True)(
        _RENDER_SPEC, post, view_hooks=_HOOKS
    )
    async_payload = await arender_for_agent(_RENDER_SPEC, post, view_hooks=_HOOKS)
    assert async_payload == sync_payload
    assert async_payload["tenant"] == "from-view-hook"
