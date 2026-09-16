"""``affordances`` — enforcement on both dispatch cores.

What a declared affordance does to a call: which refusal a caller sees, what runs
before and after it, what it costs, and what it may read. The list projection is
tested with the selector shaping; this file is the moment of the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import Prefetch, Q, QuerySet
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from rest_framework_services import (
    ActionUnavailable,
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceNotFound,
    ServiceSpec,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
)
from rest_framework_services.dispatch import utils as dispatch_utils
from tests.testapp.models import Author, Post, PublishedPost, Tag

# --- fixtures ---------------------------------------------------------------

UNPUBLISHED = Affordance(
    code="already_published",
    reason="Post 'secret draft' is already published.",
    when=Q(published=False),
)
TAGGED = Affordance(
    code="untagged",
    reason="Tag the post before publishing it.",
    # A multi-valued relation: inline, this join would duplicate rows.
    when=Q(tags__isnull=False),
)
AUTHORED = Affordance(
    code="no_author",
    reason="A post needs an author.",
    # A nullable relation: a NULL author must not read as available.
    when=Q(author__name__isnull=False),
)


class _TitleIn(serializers.Serializer):
    title = serializers.CharField(required=False)


def _publish(*, instance: Post) -> Post:
    instance.published = True
    instance.save(update_fields=["published"])
    return instance


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _spec(*affordances: Affordance, **fields: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=fields.pop("service", _publish),
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
        affordances=list(affordances) if affordances else fields.pop("affordances", None),
        **fields,
    )


def _post(**fields: Any) -> Post:
    fields.setdefault("title", "draft")
    tags = fields.pop("tags", ())
    post = Post.objects.create(**fields)
    post.tags.add(*tags)
    return post


class _DenyObject(BasePermission):
    message = "You may not touch this post."

    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


# --- the refusal ------------------------------------------------------------


@pytest.mark.django_db
def test_an_available_row_runs_the_service() -> None:
    post = _post()
    result = dispatch_spec(_spec(UNPUBLISHED), user=None, params={"pk": post.pk})

    post.refresh_from_db()
    assert post.published is True
    assert result.value.pk == post.pk


@pytest.mark.django_db
def test_an_unavailable_row_raises_with_code_and_reason_and_the_service_never_runs() -> None:
    post = _post(published=True, title="kept")

    def must_not_run(*, instance: Post) -> None:
        instance.title = "changed"
        instance.save()

    with pytest.raises(ActionUnavailable) as info:
        dispatch_spec(_spec(UNPUBLISHED, service=must_not_run), user=None, params={"pk": post.pk})

    assert info.value.code == "already_published"
    assert info.value.message == "Post 'secret draft' is already published."
    post.refresh_from_db()
    assert post.title == "kept"


def test_the_refusal_is_a_conflict_so_a_transport_that_never_heard_of_it_still_copes() -> None:
    assert issubclass(ActionUnavailable, ServiceConflict)


@pytest.mark.django_db
def test_declaration_order_decides_which_refusal_a_caller_sees() -> None:
    post = _post(published=True)  # unmet: UNPUBLISHED and TAGGED

    for declared, expected in (
        ((UNPUBLISHED, TAGGED), "already_published"),
        ((TAGGED, UNPUBLISHED), "untagged"),
    ):
        with pytest.raises(ActionUnavailable) as info:
            dispatch_spec(_spec(*declared), user=None, params={"pk": post.pk})
        assert info.value.code == expected


@pytest.mark.django_db
def test_a_later_unmet_condition_still_refuses() -> None:
    """Not only the first entry is read: an available first one must not wave the
    call through."""
    post = _post()  # UNPUBLISHED met, TAGGED unmet

    with pytest.raises(ActionUnavailable) as info:
        dispatch_spec(_spec(UNPUBLISHED, TAGGED), user=None, params={"pk": post.pk})
    assert info.value.code == "untagged"


# --- what a row condition means ----------------------------------------------


@pytest.mark.django_db
def test_a_multi_valued_relation_reads_as_filter_does() -> None:
    """Two tags is available once, not ambiguously twice."""
    tagged = _post(tags=[Tag.objects.create(name="a"), Tag.objects.create(name="b")])
    untagged = _post()

    dispatch_spec(_spec(TAGGED), user=None, params={"pk": tagged.pk})
    with pytest.raises(ActionUnavailable, match="Tag the post"):
        dispatch_spec(_spec(TAGGED), user=None, params={"pk": untagged.pk})


@pytest.mark.django_db
def test_a_negated_multi_valued_condition_reads_as_filter_does() -> None:
    """``filter(~Q(tags__name="x"))`` means "has no tag named x". Evaluated per joined
    row instead, the tag that is not ``x`` would answer "available"."""
    post = _post()
    post.tags.add(Tag.objects.create(name="y"))
    post.tags.add(Tag.objects.create(name="x"))
    not_x = Affordance(code="tagged_x", reason="Tagged x.", when=~Q(tags__name="x"))

    with pytest.raises(ActionUnavailable, match="Tagged x."):
        dispatch_spec(
            ServiceSpec(service=lambda: None, affordances=[not_x]),
            user=None,
            params={},
            instance=post,
        )


@pytest.mark.django_db
def test_a_null_is_not_true() -> None:
    authored = _post(author=Author.objects.create(name="Ann"))
    orphan = _post(author=None)

    dispatch_spec(_spec(AUTHORED), user=None, params={"pk": authored.pk})
    with pytest.raises(ActionUnavailable, match="needs an author"):
        dispatch_spec(_spec(AUTHORED), user=None, params={"pk": orphan.pk})


@pytest.mark.django_db
def test_the_check_reads_the_table_not_a_prefetched_cache() -> None:
    """A per-instance predicate reading ``instance.tags.all()`` would see the filtered
    prefetch and report the post untagged; the row condition asks the database."""
    post = _post(tags=[Tag.objects.create(name="a")])
    held = Post.objects.prefetch_related(Prefetch("tags", queryset=Tag.objects.none())).get(
        pk=post.pk
    )
    assert list(held.tags.all()) == []

    dispatch_spec(_spec(TAGGED), user=None, params={}, instance=held)


@pytest.mark.django_db
def test_a_default_manager_that_hides_the_row_does_not_decide() -> None:
    """The caller already holds the row; a manager filtering drafts must neither make
    it vanish nor make it unavailable."""
    draft = PublishedPost(title="draft", published=False)
    draft.save()
    always = Affordance(code="c", reason="r", when=Q(pk__isnull=False))

    dispatch_spec(
        ServiceSpec(service=lambda: None, affordances=[always]),
        user=None,
        params={},
        instance=draft,
    )


@pytest.mark.django_db
def test_a_row_deleted_after_resolution_is_not_found() -> None:
    post = _post()
    held = Post.objects.get(pk=post.pk)
    Post.objects.filter(pk=post.pk).delete()

    with pytest.raises(ServiceNotFound):
        dispatch_spec(
            ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED]),
            user=None,
            params={},
            instance=held,
        )


@pytest.mark.django_db
def test_a_row_condition_with_no_row_is_a_configuration_error() -> None:
    """A create resolves no row. Refused at ``as_view()`` for a mounted spec; off HTTP
    this is where it surfaces."""
    with pytest.raises(ImproperlyConfigured, match="resolved NoneType"):
        dispatch_spec(
            ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED]),
            user=None,
            params={},
        )


@pytest.mark.django_db
def test_a_row_condition_on_a_non_model_target_is_a_configuration_error() -> None:
    @dataclass
    class _Record:
        pk: int

    with pytest.raises(ImproperlyConfigured, match="resolved _Record"):
        dispatch_spec(
            ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED]),
            user=None,
            params={},
            instance=_Record(pk=1),
        )


# --- cost -----------------------------------------------------------------------


def _queries(spec: ServiceSpec[Any, Any, Any], post: Post) -> int:
    with CaptureQueriesContext(connection) as ctx:
        dispatch_spec(spec, user=None, params={}, instance=post)
    return len(ctx.captured_queries)


def _noop_service() -> None:
    return None


@pytest.mark.django_db
@pytest.mark.parametrize("declared", [None, []], ids=["undeclared", "empty"])
def test_declaring_nothing_costs_no_query(declared: Any) -> None:
    """Absolute rather than relative: this dispatch runs no query of its own at all,
    so anything the feature spent on an undeclared spec would show as one."""
    post = _post(tags=[Tag.objects.create(name="a")], author=Author.objects.create(name="A"))
    spec = ServiceSpec(service=_noop_service, atomic=False, affordances=declared)
    assert _queries(spec, post) == 0


@pytest.mark.django_db(transaction=True)
async def test_declaring_nothing_costs_the_async_path_no_executor_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hops: list[Any] = []
    real = dispatch_utils.arun_off_loop

    async def counting(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        hops.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(dispatch_utils, "arun_off_loop", counting)
    post = await Post.objects.acreate(title="draft")
    ambient = Affordance(code="open", reason="Closed.", when=lambda: True)

    await adispatch_spec(ServiceSpec(service=_noop_service), user=None, params={}, instance=post)
    assert hops == []
    await adispatch_spec(
        ServiceSpec(service=_noop_service, affordances=[ambient]),
        user=None,
        params={},
        instance=post,
    )
    assert [fn.__name__ for fn in hops] == ["call_affordances"]


@pytest.mark.django_db
def test_every_row_condition_is_answered_by_one_query() -> None:
    post = _post(tags=[Tag.objects.create(name="a")], author=Author.objects.create(name="A"))
    baseline = _queries(ServiceSpec(service=_noop_service, atomic=False), post)

    one = ServiceSpec(service=_noop_service, atomic=False, affordances=[UNPUBLISHED])
    three = ServiceSpec(
        service=_noop_service, atomic=False, affordances=[UNPUBLISHED, TAGGED, AUTHORED]
    )
    assert _queries(one, post) == baseline + 1
    assert _queries(three, post) == baseline + 1


@pytest.mark.django_db
def test_only_callable_conditions_cost_no_row_query() -> None:
    post = _post()
    baseline = _queries(ServiceSpec(service=_noop_service, atomic=False), post)
    ambient = Affordance(code="open", reason="Closed.", when=lambda: True)

    spec = ServiceSpec(service=_noop_service, atomic=False, affordances=[ambient])
    assert _queries(spec, post) == baseline


@pytest.mark.django_db
def test_an_early_callable_refusal_spends_no_row_query() -> None:
    post = _post()
    closed = Affordance(code="closed", reason="Closed.", when=lambda: False)
    spec = ServiceSpec(service=_noop_service, atomic=False, affordances=[closed, UNPUBLISHED])

    with CaptureQueriesContext(connection) as ctx, pytest.raises(ActionUnavailable):
        dispatch_spec(spec, user=None, params={}, instance=post)
    assert ctx.captured_queries == []


# --- callable conditions ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(("returned", "refused"), [(True, False), (False, True), (None, True)])
def test_a_callable_is_read_for_truth_and_nothing_returned_refuses(
    returned: Any, refused: bool
) -> None:
    post = _post()
    condition = Affordance(code="books_closed", reason="Closed.", when=lambda: returned)
    spec = ServiceSpec(service=_noop_service, affordances=[condition])

    if refused:
        with pytest.raises(ActionUnavailable, match="Closed."):
            dispatch_spec(spec, user=None, params={}, instance=post)
    else:
        dispatch_spec(spec, user=None, params={}, instance=post)


@pytest.mark.django_db
def test_a_callable_binds_the_ambient_pool_by_name() -> None:
    seen: dict[str, Any] = {}
    author = Author.objects.create(name="acting user stand-in")

    def only_for(*, user: Any) -> bool:
        seen["user"] = user
        return True

    spec = ServiceSpec(
        service=_noop_service,
        affordances=[Affordance(code="c", reason="r", when=only_for)],
    )
    dispatch_spec(spec, user=author, params={}, instance=_post())
    assert seen == {"user": author}


@pytest.mark.django_db
def test_a_catch_all_callable_never_sees_the_calls_target_or_input() -> None:
    seen: dict[str, Any] = {}

    def anything(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    spec = ServiceSpec(
        service=lambda *, instance, data: None,
        input_serializer=_TitleIn,
        affordances=[Affordance(code="c", reason="r", when=anything)],
    )
    dispatch_spec(spec, user=None, params={"title": "x"}, instance=_post())

    assert {"user", "request"} <= set(seen)
    assert not {"instance", "collection", "data", "serializer"} & set(seen)


@pytest.mark.django_db
def test_a_callable_condition_refuses_a_list_payload_bulk() -> None:
    ran: list[Any] = []
    closed = Affordance(code="closed", reason="Closed.", when=lambda: False)
    spec = ServiceSpec(
        service=lambda *, data: ran.append(data),
        input_serializer=_TitleIn,
        many=True,
        affordances=[closed],
    )

    with pytest.raises(ActionUnavailable, match="Closed."):
        dispatch_spec(spec, user=None, params=[{"title": "a"}])
    assert ran == []


@pytest.mark.django_db
def test_a_callable_condition_refuses_a_collection_target() -> None:
    ran: list[Any] = []
    closed = Affordance(code="closed", reason="Closed.", when=lambda: False)
    spec = ServiceSpec(
        service=lambda *, collection: ran.append(collection),
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=lambda: Post.objects.all()
        ),
        affordances=[closed],
    )

    with pytest.raises(ActionUnavailable, match="Closed."):
        dispatch_spec(spec, user=None, params={})
    assert ran == []


# --- ordering ----------------------------------------------------------------------


def _guarded(*affordances: Affordance, **fields: Any) -> ServiceSpec[Any, Any, Any]:
    return _spec(*affordances, permission_classes=[_DenyObject], **fields)


@pytest.mark.django_db
def test_access_is_decided_before_availability() -> None:
    """A caller who may not touch the row must not learn what state it is in.

    Sized so both refusals apply to the same call; the one that answers has to be
    the permission, and nothing of the affordance may reach the caller.
    """
    post = _post(published=True)
    context = build_offline_context(None)

    with pytest.raises(PermissionDenied) as info:
        dispatch_spec(
            _guarded(UNPUBLISHED),
            user=None,
            params={"pk": post.pk},
            request=context.request,
            view=context.view,
            on_target_resolved=enforce_permissions,
        )

    assert str(info.value.detail) == "You may not touch this post."
    assert "already published" not in str(info.value.detail)


@pytest.mark.django_db
def test_availability_is_decided_before_preconditions() -> None:
    post = _post(published=True)
    calls: list[str] = []

    with pytest.raises(ActionUnavailable):
        dispatch_spec(
            _spec(UNPUBLISHED, preconditions=[lambda: calls.append("precondition")]),
            user=None,
            params={"pk": post.pk},
        )
    assert calls == []


# --- async -------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_async_an_unavailable_row_refuses_off_the_loop() -> None:
    """The row check is a query; on the event loop it would raise
    ``SynchronousOnlyOperation`` instead of the refusal."""
    post = await Post.objects.acreate(title="kept", published=True)

    with pytest.raises(ActionUnavailable) as info:
        await adispatch_spec(_spec(UNPUBLISHED, TAGGED), user=None, params={"pk": post.pk})

    assert info.value.code == "already_published"
    await post.arefresh_from_db()
    assert post.published is True


@pytest.mark.django_db(transaction=True)
async def test_async_an_available_row_runs_the_service() -> None:
    post = await Post.objects.acreate(title="draft")

    await adispatch_spec(_spec(UNPUBLISHED), user=None, params={"pk": post.pk})

    await post.arefresh_from_db()
    assert post.published is True


@pytest.mark.django_db(transaction=True)
async def test_async_a_callable_condition_that_queries_runs_off_the_loop() -> None:
    post = await Post.objects.acreate(title="draft")

    def no_other_drafts() -> bool:
        return not Post.objects.exclude(pk=post.pk).filter(published=False).exists()

    spec = _spec(Affordance(code="c", reason="Another draft exists.", when=no_other_drafts))
    await adispatch_spec(spec, user=None, params={"pk": post.pk})

    await Post.objects.acreate(title="other draft")
    with pytest.raises(ActionUnavailable, match="Another draft exists."):
        await adispatch_spec(spec, user=None, params={"pk": post.pk})


@pytest.mark.django_db(transaction=True)
async def test_async_access_is_decided_before_availability() -> None:
    post = await Post.objects.acreate(title="draft", published=True)
    context = build_offline_context(None)

    with pytest.raises(PermissionDenied) as info:
        await adispatch_spec(
            _guarded(UNPUBLISHED),
            user=None,
            params={"pk": post.pk},
            request=context.request,
            view=context.view,
            on_target_resolved=enforce_permissions,
        )

    assert str(info.value.detail) == "You may not touch this post."
    assert "already published" not in str(info.value.detail)


@pytest.mark.django_db(transaction=True)
async def test_async_availability_is_decided_before_preconditions() -> None:
    post = await Post.objects.acreate(title="draft", published=True)
    calls: list[str] = []

    with pytest.raises(ActionUnavailable):
        await adispatch_spec(
            _spec(UNPUBLISHED, preconditions=[lambda: calls.append("precondition")]),
            user=None,
            params={"pk": post.pk},
        )
    assert calls == []


@pytest.mark.django_db(transaction=True)
async def test_async_a_callable_condition_refuses_a_list_payload_bulk() -> None:
    closed = Affordance(code="closed", reason="Closed.", when=lambda: False)
    spec = ServiceSpec(
        service=lambda *, data: None, input_serializer=_TitleIn, many=True, affordances=[closed]
    )

    with pytest.raises(ActionUnavailable, match="Closed."):
        await adispatch_spec(spec, user=None, params=[{"title": "a"}])
