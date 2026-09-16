"""Tests for the helpers in selectors/utils.py, exported and not."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.selectors.utils import (
    _filter_set_accepts_request,
    amaterialize_retrieve,
    arun_selector,
    materialize_retrieve,
    run_selector,
)
from tests.testapp.models import Post


def _sync_fn(*, value: int) -> int:
    return value * 2


async def _async_fn(*, value: int) -> int:
    return value * 3


class TestRunSelector:
    def test_sync(self) -> None:
        assert run_selector(_sync_fn, {"value": 5}) == 10

    def test_async_bridged(self) -> None:
        assert run_selector(_async_fn, {"value": 5}) == 15


class TestArunSelector:
    @pytest.mark.asyncio
    async def test_async(self) -> None:
        assert await arun_selector(_async_fn, {"value": 4}) == 12

    @pytest.mark.asyncio
    async def test_sync(self) -> None:
        assert await arun_selector(_sync_fn, {"value": 4}) == 8


class TestFilterSetAcceptsRequest:
    def test_true_when_request_declared(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any, request: Any = None) -> None: ...

        assert _filter_set_accepts_request(_FS) is True

    def test_true_when_var_keyword(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any, **kwargs: Any) -> None: ...

        assert _filter_set_accepts_request(_FS) is True

    def test_false_for_bare_data_queryset(self) -> None:
        class _FS:
            def __init__(self, *, data: Any, queryset: Any) -> None: ...

        assert _filter_set_accepts_request(_FS) is False


@pytest.mark.django_db
class TestMaterializeRetrieve:
    """The exported rule for what a ``RETRIEVE`` selector's return resolves to."""

    def test_a_queryset_resolves_to_its_first_row(self) -> None:
        post = Post.objects.create(title="only")
        assert materialize_retrieve(Post.objects.filter(pk=post.pk)) == post

    def test_an_empty_queryset_resolves_to_none(self) -> None:
        assert materialize_retrieve(Post.objects.none()) is None

    def test_anything_else_is_already_the_row(self) -> None:
        row = object()
        assert materialize_retrieve(row) is row


@pytest.mark.django_db(transaction=True)
class TestAmaterializeRetrieve:
    async def test_a_queryset_resolves_to_its_first_row(self) -> None:
        post = await Post.objects.acreate(title="only")
        assert await amaterialize_retrieve(Post.objects.filter(pk=post.pk)) == post

    async def test_anything_else_is_already_the_row(self) -> None:
        row = object()
        assert await amaterialize_retrieve(row) is row
