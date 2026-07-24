"""URL-kwarg & provider-state parity over the off-HTTP path.

Covers three behaviours end-to-end through ``dispatch_spec`` /
``adispatch_spec``:

- **Unpack delivery**: a ``**extras: Unpack[TypedDict]`` selector is a closed
  input surface, so ``UnknownArguments.REJECT`` accepts its declared keys and
  rejects strangers (schema-side reflection is covered in ``tests/jsonschema``).
- **View kwargs**: the offline view's ``kwargs`` (seeded by
  ``build_offline_context(kwargs=…)``) are spread into the selector / target
  pools — authoritative over ``params``, below a ``spec.kwargs`` provider.
- **Provider decline**: a ``spec.kwargs`` provider that returns ``UNSET`` for a
  key is declining, so a caller-supplied ``params`` value survives instead of
  being overwritten by a fallback ``None``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from tests.testapp.models import Author, Post


class _PostsInAuthorExtras(TypedDict, total=False):
    author_pk: int
    role: NotRequired[str]


def _posts_scoped_by_author(**extras: Unpack[_PostsInAuthorExtras]) -> QuerySet[Post]:
    """A nested-route selector: scope posts by the URL's ``author_pk``."""
    qs = Post.objects.all().order_by("id")
    author_pk = extras.get("author_pk")
    if author_pk is not None:
        qs = qs.filter(author_id=author_pk)
    return qs


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


@pytest.mark.django_db
class TestUrlKwargsDelivery:
    """``view.kwargs`` reach the selector off-HTTP."""

    def _two_authors(self) -> tuple[Author, Author]:
        a1 = Author.objects.create(name="a1")
        a2 = Author.objects.create(name="a2")
        Post.objects.create(title="a1-post", author=a1)
        Post.objects.create(title="a2-post", author=a2)
        return a1, a2

    def test_url_kwarg_scopes_the_selector(self) -> None:
        a1, _ = self._two_authors()
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        context = build_offline_context(user=None, params={}, kwargs={"author_pk": a1.pk})
        result = dispatch_spec(spec, user=None, params={}, view=context.view)
        assert [p.title for p in result.value] == ["a1-post"]

    def test_url_kwarg_is_authoritative_over_params(self) -> None:
        a1, a2 = self._two_authors()
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        context = build_offline_context(user=None, params={}, kwargs={"author_pk": a1.pk})
        # The client tries to widen scope to a2 via params; the route wins.
        result = dispatch_spec(spec, user=None, params={"author_pk": a2.pk}, view=context.view)
        assert [p.title for p in result.value] == ["a1-post"]

    def test_provider_still_wins_over_url_kwarg(self) -> None:
        a1, a2 = self._two_authors()

        def scope(**_: Any) -> dict[str, Any]:
            return {"author_pk": a2.pk}

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author, kwargs=scope)
        context = build_offline_context(user=None, params={}, kwargs={"author_pk": a1.pk})
        result = dispatch_spec(spec, user=None, params={}, view=context.view)
        assert [p.title for p in result.value] == ["a2-post"]

    def test_no_view_leaves_behaviour_unchanged(self) -> None:
        a1, _ = self._two_authors()
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        # No view → author_pk only reachable via params (the previous path).
        result = dispatch_spec(spec, user=None, params={"author_pk": a1.pk})
        assert [p.title for p in result.value] == ["a1-post"]

    def test_instance_selector_reads_url_kwarg_authoritatively(self) -> None:
        post = Post.objects.create(title="target")
        other = Post.objects.create(title="other")

        def svc(*, instance: Post) -> Post:
            return instance

        spec = ServiceSpec(
            service=svc,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        context = build_offline_context(user=None, params={}, kwargs={"pk": post.pk})
        # params points at ``other``; the route capture (pk=post) is authoritative.
        result = dispatch_spec(spec, user=None, params={"pk": other.pk}, view=context.view)
        assert result.value.pk == post.pk


@pytest.mark.django_db(transaction=True)
class TestUrlKwargsAsync:
    """``view.kwargs`` over the async dispatcher."""

    async def test_async_url_kwarg_scopes_the_selector(self) -> None:
        a1 = await Author.objects.acreate(name="a1")
        a2 = await Author.objects.acreate(name="a2")
        await Post.objects.acreate(title="a1-post", author=a1)
        await Post.objects.acreate(title="a2-post", author=a2)
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        context = build_offline_context(user=None, params={}, kwargs={"author_pk": a1.pk})
        result = await adispatch_spec(spec, user=None, params={}, view=context.view)
        titles = [p.title async for p in result.value]
        assert titles == ["a1-post"]

    async def test_async_instance_selector_reads_url_kwarg(self) -> None:
        post = await Post.objects.acreate(title="target")
        other = await Post.objects.acreate(title="other")

        def svc(*, instance: Post) -> Post:
            return instance

        spec = ServiceSpec(
            service=svc,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        context = build_offline_context(user=None, params={}, kwargs={"pk": post.pk})
        result = await adispatch_spec(spec, user=None, params={"pk": other.pk}, view=context.view)
        assert result.value.pk == post.pk


@pytest.mark.django_db
class TestRejectAcceptsUnpackKeys:
    """REJECT treats an Unpack surface as closed."""

    def test_reject_accepts_declared_url_kwarg(self) -> None:
        author = Author.objects.create(name="a1")
        Post.objects.create(title="a1-post", author=author)
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        # ``author_pk`` is a declared TypedDict key → not "unknown" under REJECT.
        result = dispatch_spec(
            spec,
            user=None,
            params={"author_pk": author.pk},
            unknown_arguments=UnknownArguments.REJECT,
        )
        assert [p.title for p in result.value] == ["a1-post"]

    def test_reject_raises_on_undeclared_key(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_scoped_by_author)
        with pytest.raises(ValidationError, match="bogus"):
            dispatch_spec(
                spec,
                user=None,
                params={"bogus": 1},
                unknown_arguments=UnknownArguments.REJECT,
            )


@pytest.mark.django_db
class TestProviderDecline:
    """A provider that returns ``UNSET`` steps aside."""

    def test_declined_key_lets_caller_value_survive(self) -> None:
        author = Author.objects.create(name="a1")
        Post.objects.create(title="a1-post", author=author)

        def scope(**_: Any) -> dict[str, Any]:
            # Off-HTTP this benign key can't be resolved → decline, don't stomp.
            return {"author_pk": UNSET}

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts_scoped_by_author,
            kwargs=scope,
        )
        # SPREAD_AUTHOR_WINS default: without decline, the provider's value would
        # override; UNSET lets the caller's ``author_pk`` reach the selector.
        result = dispatch_spec(
            spec,
            user=None,
            params={"author_pk": author.pk},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
        assert [p.title for p in result.value] == ["a1-post"]
