"""Tests for the helpers in selectors/utils.py, exported and not."""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.selectors.amaterialize_retrieve import (
    amaterialize_retrieve as kernel_amaterialize_retrieve,
)
from django_service_specs.selectors.arun_selector import arun_selector as kernel_arun_selector
from django_service_specs.selectors.is_queryset import is_queryset as kernel_is_queryset
from django_service_specs.selectors.materialize_retrieve import (
    materialize_retrieve as kernel_materialize_retrieve,
)
from django_service_specs.selectors.run_selector import run_selector as kernel_run_selector
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

import rest_framework_services
from rest_framework_services.selectors import utils
from tests.testapp.models import Author, Post


@pytest.mark.parametrize(
    ("name", "kernel_object"),
    [
        ("run_selector", kernel_run_selector),
        ("arun_selector", kernel_arun_selector),
        ("is_queryset", kernel_is_queryset),
        ("materialize_retrieve", kernel_materialize_retrieve),
        ("amaterialize_retrieve", kernel_amaterialize_retrieve),
    ],
)
def test_the_selector_runners_are_the_kernel_s(name: str, kernel_object: Any) -> None:
    # The kernel owns their behaviour and tests it; drfs keeps this module's path
    # and the root name.
    assert getattr(utils, name) is kernel_object
    assert getattr(rest_framework_services, name) is kernel_object


@pytest.mark.django_db
class TestApplyQuerysetShaping:
    """drfs' shaping signature, as a thin adapter over the kernel's ``shape_queryset``.

    Called directly with real django-filter ``FilterSet``s, as the view layer once
    called it; the views now shape through dispatch. The first group holds what
    the signature always meant; the second, each change the kernel brings.
    """

    def test_nothing_configured_hands_back_the_value_untouched(self) -> None:
        value = [1, 2]
        assert _shape(value) is value

    def test_the_fixed_order_and_the_positional_extend_queryset(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="shipped", author=author, published=True)
        Post.objects.create(title="draft", author=author, published=False)
        request = _request("/?published=true")
        seen: list[Any] = []

        def extend(queryset: QuerySet[Post], view: Any, req: Any) -> QuerySet[Post]:
            # The declarative fields have run already, and the filter has not.
            seen.extend([queryset.query.select_related, queryset.count(), view, req])
            return queryset.order_by("title")

        shaped = _shape(
            Post.objects.all(),
            request=request,
            select_related=["author"],
            extend_queryset=extend,
            filter_set=_Published,
        )

        assert [post.title for post in shaped] == ["shipped"]
        assert seen == [{"author": {}}, 2, _VIEW, request]

    def test_filter_data_replaces_the_query_string(self) -> None:
        Post.objects.create(title="shipped", published=True)
        Post.objects.create(title="draft", published=False)

        shaped = _shape(
            Post.objects.all(),
            request=_request("/?published=true"),
            filter_set=_Published,
            filter_data={"published": "false"},
        )

        assert [post.title for post in shaped] == ["draft"]

    def test_a_repeated_query_key_reaches_a_multi_valued_filter_whole(self) -> None:
        for title in ("a", "b", "c"):
            Post.objects.create(title=title)

        shaped = _shape(
            Post.objects.all(), request=_request("/?title=a&title=c"), filter_set=_Titles
        )

        assert sorted(post.title for post in shaped) == ["a", "c"]

    def test_a_request_scoped_filter_set_is_handed_the_request(self) -> None:
        Post.objects.create(title="ada-post", author=Author.objects.create(name="ada"))
        Post.objects.create(title="bea-post", author=Author.objects.create(name="bea"))
        request = _request("/")
        request.user = User(username="ada")

        shaped = _shape(Post.objects.all(), request=request, filter_set=_OwnedByCaller)

        assert [post.title for post in shaped] == ["ada-post"]

    def test_shaping_a_value_that_is_no_queryset_is_refused_naming_its_source(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="SelectorSpec.selector returned list"):
            _shape([1], select_related=["author"])

    # --- what the kernel's shaping changes -----------------------------------------

    def test_a_value_the_filter_set_refuses_is_the_kernel_s_invalid_arguments(self) -> None:
        # Was DRF's ValidationError; the DRF edge translates this one to DRF's at
        # its boundary. The message is django-filter's own, keyed as the
        # FilterSet keys its errors, and the kernel's leaves are plain strings.
        with pytest.raises(KernelInvalidArguments) as refused:
            _shape(Post.objects.all(), request=_request("/?status=bogus"), filter_set=_Status)

        assert refused.value.detail == {
            "status": ["Select a valid choice. bogus is not one of the available choices."]
        }

    def test_a_number_past_any_integer_column_is_refused(self) -> None:
        with pytest.raises(KernelInvalidArguments) as refused:
            _shape(
                Post.objects.all(),
                request=_request(f"/?views={2**63}"),
                filter_set=_Views,
            )

        assert set(refused.value.detail) == {"views"}

    def test_a_duck_typed_filter_set_is_refused(self) -> None:
        class _Bare:
            def __init__(self, *, data: Any, queryset: Any) -> None:
                self.qs = queryset

        with pytest.raises(ImproperlyConfigured, match="must be a django-filter FilterSet"):
            _shape(Post.objects.all(), filter_set=_Bare)

    def test_an_extend_queryset_answering_with_an_awaitable_is_refused(self) -> None:
        async def extend(queryset: Any, view: Any, request: Any) -> Any:
            return queryset

        with pytest.raises(ImproperlyConfigured, match="answered with an awaitable"):
            _shape(Post.objects.all(), extend_queryset=extend)


_VIEW = object()


def _request(path: str) -> Request:
    return Request(APIRequestFactory().get(path))


def _shape(
    qs: Any,
    *,
    request: Request | None = None,
    select_related: Any = None,
    extend_queryset: Any = None,
    filter_set: Any = None,
    filter_data: Any = None,
) -> Any:
    return utils.apply_queryset_shaping(
        qs,
        _VIEW,
        request if request is not None else _request("/"),
        select_related=select_related,
        prefetch_related=None,
        annotations=None,
        extend_queryset=extend_queryset,
        filter_set=filter_set,
        filter_data=filter_data,
        source_label="SelectorSpec.selector",
    )


class _Published(django_filters.FilterSet):
    published = django_filters.BooleanFilter()

    class Meta:
        model = Post
        fields = ["published"]


class _Titles(django_filters.FilterSet):
    title = django_filters.MultipleChoiceFilter(choices=[("a", "a"), ("b", "b"), ("c", "c")])

    class Meta:
        model = Post
        fields = ["title"]


class _Status(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(field_name="title", choices=[("draft", "draft")])

    class Meta:
        model = Post
        fields: list[str] = []


class _Views(django_filters.FilterSet):
    views = django_filters.NumberFilter()

    class Meta:
        model = Post
        fields = ["views"]


class _OwnedByCaller(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields = ["title"]

    @property
    def qs(self) -> QuerySet[Post]:
        return super().qs.filter(author__name=self.request.user.username)
