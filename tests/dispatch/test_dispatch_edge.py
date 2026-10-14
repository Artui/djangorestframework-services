"""``dispatch_spec`` as the DRF edge over the kernel's ``dispatch``.

Each test here is written against the public signature, so it runs against the
engine ``dispatch_spec`` replaced as well; the ones marked as behaviour changes
fail there.
"""

from __future__ import annotations

from typing import Annotated, Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.http import QueryDict
from django.test import override_settings
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.types.view_hooks import ViewHooks
from tests.testapp.models import Author, Post


def _posts(*, title: str | None = None) -> QuerySet[Post]:
    queryset = Post.objects.order_by("pk")
    return queryset.filter(title=title) if title is not None else queryset


def _create(*, data: Any) -> Post:
    return Post.objects.create(**data)


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


class TestRetiredModes:
    """Behaviour changes: both were modes; each is now refused by name."""

    def test_passthrough_is_refused(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, permission_classes=[])
        with pytest.raises(ValueError, match="PASSTHROUGH"):
            dispatch_spec(
                spec, user=None, params={}, unknown_arguments=UnknownArguments.PASSTHROUGH
            )

    def test_spread_caller_wins_is_refused_naming_the_replacement(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, permission_classes=[])
        with pytest.raises(ValueError, match=r"SPREAD_CALLER_WINS.*ArgumentBinding\.AUTO"):
            dispatch_spec(
                spec,
                user=None,
                params={},
                argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
            )


@pytest.mark.django_db
class TestTheTargetGuard:
    def test_a_retrieve_selector_is_guarded_with_its_row(self) -> None:
        post = Post.objects.create(title="a")
        seen: list[Any] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen.append((spec, context.user, instance))

        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: Post.objects.filter(pk=pk),
            permission_classes=[],
        )
        result = dispatch_spec(spec, user=None, params={"pk": post.pk}, on_target_resolved=guard)
        assert result.value == post
        assert seen == [(spec, None, post)]

    def test_a_list_selector_is_guarded_with_its_set_before_the_authors_preconditions(
        self,
    ) -> None:
        Post.objects.create(title="a")
        order: list[str] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            order.append(f"guard:{type(instance).__name__}")

        def precondition(*, collection: Any) -> None:
            order.append("precondition")

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts,
            preconditions=[precondition],
            permission_classes=[],
        )
        dispatch_spec(spec, user=None, params={}, on_target_resolved=guard)
        assert order == ["guard:QuerySet", "precondition"]

    def test_a_guard_refusal_stops_an_update_before_its_service(self) -> None:
        post = Post.objects.create(title="a")
        ran: list[Any] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            raise PermissionError(instance)

        spec = ServiceSpec(
            service=lambda *, instance, data: ran.append(instance),
            input_serializer=_TitleIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: Post.objects.filter(pk=pk)
            ),
            permission_classes=[],
        )
        with pytest.raises(PermissionError) as raised:
            dispatch_spec(
                spec, user=None, params={"pk": post.pk, "title": "b"}, on_target_resolved=guard
            )
        assert raised.value.args == (post,)
        assert ran == []

    def test_a_create_is_guarded_with_none(self) -> None:
        seen: list[Any] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen.append(instance)

        spec = ServiceSpec(service=_create, input_serializer=_TitleIn, permission_classes=[])
        result = dispatch_spec(spec, user=None, params={"title": "b"}, on_target_resolved=guard)
        assert seen == [None]
        assert result.value.title == "b"

    def test_instance_none_on_a_spec_with_a_lookup_is_not_found(self) -> None:
        """Behaviour change: ``None`` stands in for the lookup, which admits no missing row.

        The row ``pk`` names exists, so a lookup run anyway would find it and
        update it: ``None`` is the caller's answer, and the lookup does not run.
        """
        post = Post.objects.create(title="a")
        ran: list[Any] = []
        spec = ServiceSpec(
            service=lambda *, instance, data: ran.append(instance),
            input_serializer=_TitleIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: Post.objects.filter(pk=pk)
            ),
            permission_classes=[],
        )
        result = dispatch_spec(spec, user=None, params={"pk": post.pk, "title": "b"}, instance=None)
        assert result.kind == "not_found"
        assert ran == []

    def test_instance_none_on_a_spec_with_no_lookup_is_a_create(self) -> None:
        seen: list[Any] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen.append(instance)

        spec = ServiceSpec(service=_create, input_serializer=_TitleIn, permission_classes=[])
        result = dispatch_spec(
            spec, user=None, params={"title": "b"}, instance=None, on_target_resolved=guard
        )
        assert seen == [None]
        assert result.value.title == "b"

    def test_a_row_the_caller_resolved_is_the_services_instance_and_the_guards(self) -> None:
        post = Post.objects.create(title="a")
        seen: list[Any] = []

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen.append(instance)

        def update(*, instance: Post, data: Any) -> Post:
            instance.title = data["title"]
            instance.save()
            return instance

        spec = ServiceSpec(service=update, input_serializer=_TitleIn, permission_classes=[])
        result = dispatch_spec(
            spec, user=None, params={"title": "b"}, instance=post, on_target_resolved=guard
        )
        assert seen == [post]
        assert result.instance == post
        post.refresh_from_db()
        assert post.title == "b"


@pytest.mark.django_db
class TestDeclaredChannels:
    def _spec(self, seen: dict[str, Any]) -> SelectorSpec[Any, Any]:
        def scope(*, view: Any) -> dict[str, Any]:
            seen["view_kwarg"] = view.kwargs.get("author_pk")
            return {}

        def posts(*, author_pk: int) -> QuerySet[Post]:
            seen["argument"] = author_pk
            return Post.objects.filter(author_id=author_pk)

        return SelectorSpec(
            kind=SelectorKind.LIST,
            selector=posts,
            kwargs=scope,
            reads=[UrlKwarg("author_pk", type="integer", required=True)],
            permission_classes=[],
        )

    def test_a_url_kwarg_sent_as_an_argument_reaches_view_kwargs(self) -> None:
        """Behaviour change: the provider reading ``view.kwargs`` sees the argument."""
        author = Author.objects.create(name="a")
        seen: dict[str, Any] = {}
        context = build_offline_context(None)
        dispatch_spec(
            self._spec(seen),
            user=None,
            params={"author_pk": author.pk},
            request=context.request,
            view=context.view,
        )
        assert seen == {"view_kwarg": author.pk, "argument": author.pk}

    def test_a_url_kwarg_on_view_kwargs_reaches_the_arguments(self) -> None:
        author = Author.objects.create(name="a")
        seen: dict[str, Any] = {}
        context = build_offline_context(None, kwargs={"author_pk": str(author.pk)})
        dispatch_spec(
            self._spec(seen), user=None, params={}, request=context.request, view=context.view
        )
        # A route capture is a string, read as the integer the declaration names.
        assert seen["argument"] == author.pk

    def test_a_query_param_sent_as_an_argument_reaches_the_query_string(self) -> None:
        """Behaviour change: the request a provider reads carries the argument."""
        seen: dict[str, Any] = {}

        def scope(*, request: Any) -> dict[str, Any]:
            seen["query"] = request.query_params.get("fields")
            return {}

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts,
            kwargs=scope,
            reads=[QueryParam("fields")],
            permission_classes=[],
        )
        context = build_offline_context(None)
        dispatch_spec(
            spec, user=None, params={"fields": "a"}, request=context.request, view=context.view
        )
        assert seen == {"query": "a"}

    def _query_spec(self, seen: dict[str, Any]) -> SelectorSpec[Any, Any]:
        def scope(*, request: Any) -> dict[str, Any]:
            seen["fields"] = request.query_params.get("fields")
            seen["page"] = request.query_params.get("page")
            return {}

        return SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts,
            kwargs=scope,
            reads=[QueryParam("fields"), QueryParam("page")],
            permission_classes=[],
        )

    def test_a_query_string_value_is_never_rewritten_by_an_argument(self) -> None:
        seen: dict[str, Any] = {}
        context = build_offline_context(None, query_params={"fields": "q"})
        dispatch_spec(
            self._query_spec(seen),
            user=None,
            params={"fields": "a"},
            request=context.request,
            view=context.view,
        )
        assert seen == {"fields": "q", "page": None}

    def test_only_the_names_the_query_string_lacks_are_added(self) -> None:
        seen: dict[str, Any] = {}
        context = build_offline_context(None, query_params={"fields": "q"})
        dispatch_spec(
            self._query_spec(seen),
            user=None,
            params={"fields": "a", "page": "2"},
            request=context.request,
            view=context.view,
        )
        assert seen == {"fields": "q", "page": "2"}

    def test_a_url_kwarg_sent_nowhere_leaves_view_kwargs_alone(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts,
            reads=[UrlKwarg("author_pk", type="integer")],
            permission_classes=[],
        )
        context = build_offline_context(None)
        dispatch_spec(spec, user=None, params={}, request=context.request, view=context.view)
        assert context.view.kwargs == {}


class _TitleFilter(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields = ["title"]


@pytest.mark.django_db
class TestFilterData:
    def test_filter_data_reaches_the_filter_set_and_nothing_else(self) -> None:
        Post.objects.create(title="a")
        Post.objects.create(title="b")
        seen: dict[str, Any] = {}

        def posts(*, body: str = "") -> QuerySet[Post]:
            seen["body"] = body
            return Post.objects.order_by("pk")

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=posts, filter_set=_TitleFilter, permission_classes=[]
        )
        result = dispatch_spec(
            spec,
            user=None,
            params={},
            filter_data=QueryDict("title=b&body=x"),
        )
        assert [post.title for post in result.value] == ["b"]
        assert seen == {"body": ""}

    def test_no_filter_data_leaves_the_arguments_to_filter(self) -> None:
        Post.objects.create(title="a")
        Post.objects.create(title="b")
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Post.objects.order_by("pk"),
            filter_set=_TitleFilter,
            permission_classes=[],
        )
        result = dispatch_spec(spec, user=None, params={"title": "a"})
        assert [post.title for post in result.value] == ["a"]

    def test_arguments_that_are_no_object_take_no_filter_data(self) -> None:
        """They are left for the shape check to refuse, filter data or not."""
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Post.objects.order_by("pk"),
            filter_set=_TitleFilter,
            permission_classes=[],
        )
        with pytest.raises(ValidationError) as excinfo:
            dispatch_spec(spec, user=None, params=["a"], filter_data={"title": "a"})
        assert excinfo.value.detail == {"non_field_errors": ["Expected an object."]}


@pytest.mark.django_db
class TestPoolSeeds:
    def test_a_registered_view_clashes_with_the_edges_own(self) -> None:
        """Behaviour change: the edge registers ``request`` and ``view`` itself."""
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, permission_classes=[])
        seeds = DEFAULT_POOL_SEEDS.extend(view=lambda: None)
        with pytest.raises(ImproperlyConfigured, match="'view'"):
            dispatch_spec(spec, user=None, params={}, pool_seeds=seeds)

    def test_a_resolver_declaring_request_receives_the_calls(self) -> None:
        context = build_offline_context(None)
        seen: dict[str, Any] = {}

        # Marked, or it is reflected as an argument and meets the seed of its name.
        def posts(*, tenant: Annotated[Any, NotClientInput]) -> QuerySet[Post]:
            seen["tenant"] = tenant
            return Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=posts, permission_classes=[])
        seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda *, request: request)
        dispatch_spec(
            spec,
            user=None,
            params={},
            pool_seeds=seeds,
            request=context.request,
            view=context.view,
        )
        assert seen["tenant"] is context.request

    def test_no_user_acts_as_the_unauthenticated_user(self) -> None:
        """Behaviour change: a callable reading ``user`` is handed DRF's unauthenticated user."""
        seen: dict[str, Any] = {}

        def posts(*, user: Any) -> QuerySet[Post]:
            seen["user"] = user
            return Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=posts, permission_classes=[])
        dispatch_spec(spec, user=None, params={})
        assert isinstance(seen["user"], AnonymousUser)

    @pytest.mark.parametrize("core", ["sync", "async"])
    def test_a_project_with_no_unauthenticated_user_still_acts_as_anonymous(
        self, core: str
    ) -> None:
        """``UNAUTHENTICATED_USER = None`` leaves DRF's request user ``None``; the
        kernel takes no ``None`` principal (``adispatch`` raises ``TypeError``), so the
        edge acts as Django's ``AnonymousUser`` there, on both cores alike."""
        seen: dict[str, Any] = {}

        def posts(*, user: Any) -> QuerySet[Post]:
            seen["user"] = user
            return Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=posts, permission_classes=[])
        with override_settings(REST_FRAMEWORK={"UNAUTHENTICATED_USER": None}):
            if core == "sync":
                dispatch_spec(spec, user=None, params={})
            else:
                async_to_sync(adispatch_spec)(spec, user=None, params={})
        assert isinstance(seen["user"], AnonymousUser)

    @pytest.mark.parametrize("core", ["sync", "async"])
    def test_the_guard_still_sees_no_user(self, core: str) -> None:
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any) -> None:
            seen["user"] = context.user

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts, permission_classes=[])
        with override_settings(REST_FRAMEWORK={"UNAUTHENTICATED_USER": None}):
            if core == "sync":
                dispatch_spec(spec, user=None, params={}, on_target_resolved=guard)
            else:
                async_to_sync(adispatch_spec)(spec, user=None, params={}, on_target_resolved=guard)
        assert seen == {"user": None}


@pytest.mark.django_db
class TestBareRowRefusals:
    def test_a_refusal_beside_the_rows_keeps_its_key(self) -> None:
        """Only a refusal of the rows alone is unwrapped into DRF's list shape."""
        spec = ServiceSpec(
            service=lambda *, data: [Post.objects.create(**row) for row in data],
            input_serializer=_TitleIn,
            many=True,
            reads=[UrlKwarg("author_pk", type="integer", required=True)],
            permission_classes=[],
        )
        with pytest.raises(ValidationError) as raised:
            dispatch_spec(spec, user=None, params=[{"title": "a"}])
        assert isinstance(raised.value.detail, dict)
        assert "author_pk" in raised.value.detail


class _TitleTenantIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()


def _echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


@pytest.mark.django_db
class TestServerInput:
    """``ServiceSpec.input_data`` is evaluated by the edge and merged over the arguments."""

    def test_the_input_data_provider_runs_once_per_call(self) -> None:
        calls: list[None] = []

        def provide() -> dict[str, Any]:
            calls.append(None)
            return {"tenant": "server"}

        spec = ServiceSpec(
            service=_echo,
            input_serializer=_TitleTenantIn,
            input_data=provide,
            permission_classes=[],
        )
        result = dispatch_spec(spec, user=None, params={"title": "t"})
        assert result.value == {"title": "t", "tenant": "server"}
        assert len(calls) == 1

    def test_an_unset_key_is_declined_and_the_callers_stands(self) -> None:
        spec = ServiceSpec(
            service=_echo,
            input_serializer=_TitleTenantIn,
            input_data=lambda: {"tenant": UNSET},
            permission_classes=[],
        )
        result = dispatch_spec(spec, user=None, params={"title": "t", "tenant": "client"})
        assert result.value == {"title": "t", "tenant": "client"}

    def test_the_provider_is_handed_the_row_the_call_acts_on(self) -> None:
        post = Post.objects.create(title="row")
        seen: dict[str, Any] = {}

        def provide(*, instance: Any, view: Any, request: Any) -> dict[str, Any]:
            seen.update(instance=instance, view=view, request=request)
            return {"tenant": instance.title}

        def update(*, instance: Post, data: Any) -> dict[str, Any]:
            return dict(data)

        context = build_offline_context(None, {})
        spec = ServiceSpec(
            service=update,
            input_serializer=_TitleTenantIn,
            input_data=provide,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: Post.objects.filter(pk=pk)
            ),
            permission_classes=[],
        )
        result = dispatch_spec(
            spec,
            user=None,
            params={"pk": post.pk, "title": "t"},
            request=context.request,
            view=context.view,
        )
        assert result.value == {"title": "t", "tenant": "row"}
        assert seen == {"instance": post, "view": context.view, "request": context.request}

    def test_the_specs_value_wins_over_the_view_hooks(self) -> None:
        spec = ServiceSpec(
            service=_echo,
            input_serializer=_TitleTenantIn,
            input_data=lambda: {"tenant": "spec"},
            permission_classes=[],
        )
        result = dispatch_spec(
            spec,
            user=None,
            params={"title": "t"},
            view_hooks=ViewHooks(input_data={"tenant": "hook", "title": "hook"}),
        )
        assert result.value == {"title": "hook", "tenant": "spec"}


# --- UnknownArguments.REJECT, answered as this package always answered it ------


def _scoped(*, pk: int, **filters: Any) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _keep_pk(*, pk: int) -> int:
    return pk


def _keep(*, instance: Post) -> str:
    return instance.title


class TestRejectAtTheEdge:
    def _reject(self, spec: Any, params: Any, **kwargs: Any) -> Any:
        return dispatch_spec(
            spec, user=None, params=params, unknown_arguments=UnknownArguments.REJECT, **kwargs
        )

    def test_unknown_keys_are_named_in_one_message(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: pk)

        with pytest.raises(ValidationError) as excinfo:
            self._reject(spec, {"pk": 1, "zeta": 1, "alpha": 2})

        assert excinfo.value.detail == {
            "non_field_errors": ["Unexpected argument(s): 'alpha', 'zeta'."]
        }

    def test_a_field_refusal_beside_an_unknown_key_keeps_its_own_key(self) -> None:
        class _Row(serializers.Serializer):
            quantity = serializers.IntegerField()

        spec = ServiceSpec(
            service=lambda *, data: data, input_serializer=_Row, many=True, atomic=False
        )

        with pytest.raises(ValidationError) as excinfo:
            self._reject(
                spec,
                {"items": [{"quantity": 1, "zeta": 1}, {"quantity": "x"}]},
                many_as_argument=True,
            )

        assert excinfo.value.detail == {
            "items": {
                0: {"non_field_errors": ["Unexpected argument(s): 'zeta'."]},
                1: {"quantity": ["Expected an integer."]},
            }
        }

    @pytest.mark.django_db
    def test_an_open_selector_takes_an_undeclared_key_and_drops_it(self) -> None:
        post = Post.objects.create(title="p")
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped)

        assert self._reject(spec, {"pk": post.pk, "colour": "red"}).value == post

    @pytest.mark.django_db
    def test_an_open_target_lookup_takes_an_undeclared_key_and_drops_it(self) -> None:
        post = Post.objects.create(title="p")
        spec = ServiceSpec(
            service=_keep,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scoped),
            atomic=False,
        )

        assert self._reject(spec, {"pk": post.pk, "colour": "red"}).value == "p"

    @pytest.mark.django_db
    def test_a_closed_lookup_still_refuses_an_undeclared_key(self) -> None:
        post = Post.objects.create(title="p")
        spec = ServiceSpec(
            service=_keep,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: Post.objects.filter(pk=pk)
            ),
            atomic=False,
        )

        with pytest.raises(ValidationError) as excinfo:
            self._reject(spec, {"pk": post.pk, "colour": "red"})

        assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'colour'."]}

    @pytest.mark.parametrize("name", ["user", "progress", "data"])
    def test_a_built_in_seed_name_is_not_unexpected(self, name: str) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: pk)

        assert self._reject(spec, {"pk": 1, name: "spoofed"}).value == 1

    def test_the_view_is_unexpected_though_the_edge_seeds_it(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: pk)

        with pytest.raises(ValidationError) as excinfo:
            self._reject(spec, {"pk": 1, "view": "spoofed"})

        assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'view'."]}

    def test_a_seed_name_inside_a_row_is_not_unexpected(self) -> None:
        class _Row(serializers.Serializer):
            title = serializers.CharField()

        spec = ServiceSpec(
            service=lambda *, data: [dict(row) for row in data],
            input_serializer=_Row,
            many=True,
            atomic=False,
        )

        result = self._reject(spec, [{"title": "a", "user": "spoofed"}])

        assert result.value == [{"title": "a"}]

    def test_a_refusal_with_nothing_to_reshape_leaves_as_translated(self) -> None:
        # Raised from the kernel's own refusal, so the traceback keeps where
        # dispatch refused; a reshaped one is raised from the translated one.
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_keep_pk)

        with pytest.raises(ValidationError) as excinfo:
            self._reject(spec, {"pk": "x"})

        cause = excinfo.value.__cause__
        assert isinstance(cause, KernelInvalidArguments)
        assert not isinstance(cause, ValidationError)
