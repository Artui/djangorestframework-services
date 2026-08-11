"""A nested write must not reach a row the parent does not own.

The child loop matches incoming rows against ``getattr(parent, relation).all()``,
which is correctly scoped to the parent -- but only the *match* is scoped. A row
that failed to match fell through to the create branch, which builds
``Model(**item, fk=parent)`` and saves it. Django turns a ``save()`` carrying an
explicit primary key into an **UPDATE**, so a payload naming somebody else's pk
reassigned that row to the caller's parent and overwrote its fields.

The parent scoping made the read safe and did nothing about the write that
followed it.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input
from rest_framework_services.mutations.update_from_input import update_from_input
from rest_framework_services.types.child_spec import ChildSpec
from tests.testapp.models import Author, Post

POSTS = {"posts": ChildSpec(model=Post, fk="author", match_key="id")}


@pytest.mark.django_db
class TestAPayloadCannotNameAnotherParentsRow:
    def test_the_row_is_neither_reassigned_nor_overwritten(self) -> None:
        victim = Author.objects.create(name="victim")
        attacker = Author.objects.create(name="attacker")
        theirs = Post.objects.create(title="secret", author=victim)

        with pytest.raises(ServiceValidationError) as exc:
            update_from_input(
                attacker,
                {"name": "attacker", "posts": [{"id": theirs.pk, "title": "pwned"}]},
                children=POSTS,
            )

        theirs.refresh_from_db()
        assert theirs.author_id == victim.pk
        assert theirs.title == "secret"
        assert Post.objects.count() == 1
        # The message names the relation, so a caller can find the offending key.
        assert "posts" in str(exc.value.detail)

    def test_a_primary_key_that_exists_nowhere_is_refused_as_well(self) -> None:
        # Not an authorization question -- the caller named a row and there is
        # none. Creating a different one silently would do the opposite of what
        # was asked, and would hand back a pk the caller never chose.
        author = Author.objects.create(name="a")

        with pytest.raises(ServiceValidationError):
            update_from_input(
                author,
                {"name": "a", "posts": [{"id": 4242, "title": "t"}]},
                children=POSTS,
            )
        assert Post.objects.count() == 0

    def test_field_map_cannot_smuggle_the_primary_key_in(self) -> None:
        # field_map runs before the model sees the payload, so an input key
        # routed onto the pk is the same hazard wearing a different name.
        victim = Author.objects.create(name="victim")
        attacker = Author.objects.create(name="attacker")
        theirs = Post.objects.create(title="secret", author=victim)
        spec = {
            "posts": ChildSpec(
                model=Post,
                fk="author",
                match_key="ref",
                field_map={"ref": "id"},
            )
        }

        with pytest.raises(ServiceValidationError):
            update_from_input(
                attacker,
                {"name": "attacker", "posts": [{"ref": theirs.pk, "title": "pwned"}]},
                children=spec,
            )

        theirs.refresh_from_db()
        assert theirs.author_id == victim.pk

    def test_a_stray_pk_is_refused_even_when_the_match_key_is_natural(self) -> None:
        # The hazard is the primary key reaching the create, not the match key.
        # A spec matching on a natural key still must not accept a pk beside it.
        victim = Author.objects.create(name="victim")
        attacker = Author.objects.create(name="attacker")
        theirs = Post.objects.create(title="secret", author=victim)
        spec = {"posts": ChildSpec(model=Post, fk="author", match_key="title")}

        with pytest.raises(ServiceValidationError):
            update_from_input(
                attacker,
                {"name": "attacker", "posts": [{"title": "brand new", "id": theirs.pk}]},
                children=spec,
            )

        theirs.refresh_from_db()
        assert theirs.author_id == victim.pk


@pytest.mark.django_db
class TestTheLegitimateCasesStillWork:
    def test_a_row_the_parent_owns_is_updated(self) -> None:
        author = Author.objects.create(name="a")
        mine = Post.objects.create(title="before", author=author)

        update_from_input(
            author,
            {"name": "a", "posts": [{"id": mine.pk, "title": "after"}]},
            children=POSTS,
        )

        mine.refresh_from_db()
        assert mine.title == "after"

    def test_a_row_with_no_identifier_is_created(self) -> None:
        author = Author.objects.create(name="a")

        update_from_input(
            author,
            {"name": "a", "posts": [{"title": "fresh"}]},
            children=POSTS,
        )

        assert Post.objects.get(title="fresh").author_id == author.pk

    def test_a_natural_match_key_still_upserts(self) -> None:
        # The reason this refuses a primary key rather than any unmatched
        # match key: a natural key that does not match is a genuine create, and
        # declaring one is how a caller gets upsert semantics.
        author = Author.objects.create(name="a")
        spec = {"posts": ChildSpec(model=Post, fk="author", match_key="title")}

        update_from_input(
            author,
            {"name": "a", "posts": [{"title": "brand new"}]},
            children=spec,
        )

        assert Post.objects.get(title="brand new").author_id == author.pk


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathBehavesIdentically:
    """Transactional, because an async ORM call runs on a different connection
    than a non-transactional wrapper rolls back -- rows written here would
    otherwise survive into later tests."""

    async def test_the_async_path_refuses_it_too(self) -> None:
        victim = await Author.objects.acreate(name="victim")
        attacker = await Author.objects.acreate(name="attacker")
        theirs = await Post.objects.acreate(title="secret", author=victim)

        with pytest.raises(ServiceValidationError):
            await aupdate_from_input(
                attacker,
                {"name": "attacker", "posts": [{"id": theirs.pk, "title": "pwned"}]},
                children=POSTS,
            )

        refreshed = await Post.objects.aget(pk=theirs.pk)
        assert refreshed.author_id == victim.pk
        assert refreshed.title == "secret"

    async def test_the_async_path_still_creates_and_updates(self) -> None:
        author = await Author.objects.acreate(name="a")
        mine = await Post.objects.acreate(title="before", author=author)

        await aupdate_from_input(
            author,
            {
                "name": "a",
                "posts": [{"id": mine.pk, "title": "after"}, {"title": "fresh"}],
            },
            children=POSTS,
        )

        refreshed = await Post.objects.aget(pk=mine.pk)
        assert refreshed.title == "after"
        assert await sync_to_async(Post.objects.filter(author=author).count)() == 2


@pytest.mark.django_db
class TestAServiceOwnedRowIsGuardedToo:
    """A declared ``create_service`` receives the payload, so the check has to
    run before the dispatch rather than inside the default helper.

    Otherwise the row writer is bypassed and the primary key reaches user code,
    which is free to persist it -- moving the same defect one layer out and
    into a place the library cannot see.
    """

    def test_the_service_is_never_handed_another_parents_primary_key(self) -> None:
        victim = Author.objects.create(name="victim")
        attacker = Author.objects.create(name="attacker")
        theirs = Post.objects.create(title="secret", author=victim)
        seen: list[dict] = []

        def create_post(*, data, **_):
            seen.append(dict(data))
            return Post.objects.create(title=data["title"], author=data["author"])

        spec = {
            "posts": ChildSpec(
                model=Post,
                fk="author",
                match_key="id",
                create_service=create_post,
            )
        }

        with pytest.raises(ServiceValidationError):
            update_from_input(
                attacker,
                {"name": "attacker", "posts": [{"id": theirs.pk, "title": "pwned"}]},
                children=spec,
            )

        assert seen == [], "the service was reached with a foreign primary key"
        theirs.refresh_from_db()
        assert theirs.author_id == victim.pk
        assert theirs.title == "secret"
