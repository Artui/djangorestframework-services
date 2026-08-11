"""Forward FK / one-to-one, and reverse one-to-one.

The three relations that hold exactly one row. The forward pair is written
before the parent is saved, which is what lets the assignment ride the ordinary
diff and ``update_fields`` machinery; the reverse one is the children loop with
the collection taken out.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_save

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    ReverseOneToOneSpec,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.mutations.utils import adelete_relations, delete_relations
from tests.testapp.models import Author, Catalog, Cover, Note, Post, Profile, Section, Tag


class _Saves:
    """Every ``post_save`` for one model, with the ``update_fields`` it carried."""

    def __init__(self, model: type[Any]) -> None:
        self.model = model
        self.calls: list[frozenset[str] | None] = []

    def __enter__(self) -> _Saves:
        post_save.connect(self._on_save, sender=self.model)
        return self

    def __exit__(self, *_: Any) -> None:
        post_save.disconnect(self._on_save, sender=self.model)

    def _on_save(self, sender: Any, update_fields: Any = None, **_: Any) -> None:
        self.calls.append(update_fields)


@pytest.mark.django_db
class TestForwardRelation:
    def test_create_writes_the_target_first_and_assigns_it(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author)},
        )
        author = Author.objects.get()
        assert result.instance.author == author
        change = result.get_relation_change("author")
        assert (change.outcome, change.pk) == ("created", author.pk)
        # The assignment is an ordinary field change, not a separate concept.
        assert result.get_field_change("author").new == author

    def test_the_target_exists_before_a_non_nullable_parent_is_saved(self) -> None:
        # Cover.catalog cannot be null, so this only works if the forward phase
        # really does run before the parent's save.
        result = create_from_input(
            Cover,
            {"image": "cover.png", "catalog": {"name": "c"}},
            relations={"catalog": ForwardRelationSpec(model=Catalog)},
        )
        assert result.instance.catalog == Catalog.objects.get()

    def test_a_one_to_one_field_takes_the_same_path(self) -> None:
        # OneToOneField subclasses ForeignKey; the spec does not distinguish.
        result = create_from_input(
            Profile,
            {"bio": "b", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author)},
        )
        assert result.instance.author == Author.objects.get()

    def test_update_rides_the_minimal_save(self) -> None:
        post = Post.objects.create(title="t")
        with _Saves(Post) as saves:
            result = update_from_input(
                post,
                {"author": {"name": "Ursula"}},
                relations={"author": ForwardRelationSpec(model=Author)},
            )
        # One save of the parent, and the FK column travelled in update_fields
        # like any other changed column -- no second save, no extra plumbing.
        assert len(saves.calls) == 1
        assert saves.calls[0] == frozenset({"author"})
        assert result.get_field_change("author").new == Author.objects.get()
        assert result.get_relation_change("author").outcome == "created"

    def test_none_clears_the_column_and_leaves_the_row(self) -> None:
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t", author=author)
        with _Saves(Post) as saves:
            result = update_from_input(
                post, {"author": None}, relations={"author": ForwardRelationSpec(model=Author)}
            )
        assert saves.calls == [frozenset({"author"})]
        post.refresh_from_db()
        assert post.author_id is None
        # The row a forward relation points at is not the parent's to remove.
        assert Author.objects.filter(pk=author.pk).exists()
        change = result.get_relation_change("author")
        assert (change.outcome, change.pk) == ("cleared", None)

    def test_omitting_the_relation_touches_nothing(self) -> None:
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t", author=author)
        with _Saves(Post) as saves:
            result = update_from_input(
                post, {}, relations={"author": ForwardRelationSpec(model=Author)}
            )
        assert saves.calls == []
        post.refresh_from_db()
        assert post.author_id == author.pk
        change = result.get_relation_change("author")
        assert (change.outcome, bool(change)) == ("untouched", False)


@pytest.mark.django_db
class TestForwardScope:
    def test_a_queryset_scope_matches_and_updates(self) -> None:
        author = Author.objects.create(name="Ursula")
        result = create_from_input(
            Post,
            {"title": "t", "author": {"pk": author.pk, "name": "Ursula K."}},
            relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
        )
        author.refresh_from_db()
        assert author.name == "Ursula K."
        assert result.get_relation_change("author").outcome == "updated"
        assert Author.objects.count() == 1

    def test_a_callable_scope_is_resolved_from_the_caller_pool(self) -> None:
        mine = Author.objects.create(name="mine")
        theirs = Author.objects.create(name="theirs")

        def owned_by(*, user: str) -> Any:
            return Author.objects.filter(name=user)

        result = create_from_input(
            Post,
            {"title": "t", "author": {"pk": mine.pk, "name": "renamed"}},
            relations={"author": ForwardRelationSpec(model=Author, scope=owned_by)},
            context={"user": "mine", "request": object()},
        )
        assert result.get_relation_change("author").pk == mine.pk
        mine.refresh_from_db()
        assert mine.name == "renamed"
        theirs.refresh_from_db()
        assert theirs.name == "theirs"

    def test_a_key_outside_the_scope_is_refused_not_created(self) -> None:
        # Creating here would write the payload -- pk included -- and Django's
        # save() would land it on that very row, reaching past the scope.
        theirs = Author.objects.create(name="theirs")
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"pk": theirs.pk, "name": "renamed"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, scope=Author.objects.filter(name="mine")
                    )
                },
            )
        assert "outside the scope" in excinfo.value.detail["author"][0]
        theirs.refresh_from_db()
        assert theirs.name == "theirs"
        assert not Post.objects.exists()

    def test_a_custom_match_key(self) -> None:
        author = Author.objects.create(name="Ursula")
        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={
                "author": ForwardRelationSpec(
                    model=Author, match_key="name", scope=Author.objects.all()
                )
            },
        )
        assert result.get_relation_change("author").pk == author.pk

    def test_no_key_is_create_only_even_unscoped(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author)},
        )
        assert result.get_relation_change("author").outcome == "created"

    def test_an_unscoped_spec_meeting_a_match_key_raises(self) -> None:
        author = Author.objects.create(name="Ursula")
        with pytest.raises(ImproperlyConfigured) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"pk": author.pk, "name": "renamed"}},
                relations={"author": ForwardRelationSpec(model=Author)},
            )
        message = str(excinfo.value)
        assert "relations['author']" in message  # names the relation
        assert "Declare scope=" in message  # names the remedy
        assert "guessing a key" in message  # says why
        author.refresh_from_db()
        assert author.name == "Ursula"


@pytest.mark.django_db
class TestForwardServices:
    def test_a_create_service_owns_the_target_row_and_sees_no_parent(self) -> None:
        seen: dict[str, Any] = {}

        def create_author(**pool: Any) -> Author:
            seen.update(pool)
            return Author.objects.create(name=pool["data"]["name"].upper())

        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author, create_service=create_author)},
            context={"user": "caller"},
        )
        assert seen["user"] == "caller"
        assert seen["data"] == {"name": "Ursula"}
        # There is no parent yet -- that is what "before the parent's save" means.
        assert "parent" not in seen
        assert result.instance.author.name == "URSULA"

    def test_an_update_service_may_return_none(self) -> None:
        author = Author.objects.create(name="Ursula")

        def rename(*, instance: Author, data: Any) -> None:
            instance.name = data["name"]
            instance.save(update_fields=["name"])

        result = create_from_input(
            Post,
            {"title": "t", "author": {"pk": author.pk, "name": "renamed"}},
            relations={
                "author": ForwardRelationSpec(
                    model=Author, scope=Author.objects.all(), update_service=rename
                )
            },
        )
        assert result.instance.author == author
        author.refresh_from_db()
        assert author.name == "renamed"

    def test_row_shaping_reaches_the_target(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "author": {"full_name": "Ursula", "note": "drop me"}},
            relations={
                "author": ForwardRelationSpec(
                    model=Author, field_map={"full_name": "name"}, exclude_fields=["note"]
                )
            },
        )
        assert result.instance.author.name == "Ursula"


@pytest.mark.django_db
class TestReverseOneToOne:
    def test_create_writes_and_links_the_row(self) -> None:
        result = create_from_input(
            Author,
            {"name": "Ursula", "profile": {"bio": "b"}},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )
        profile = Profile.objects.get()
        assert profile.author == result.instance
        change = result.get_relation_change("profile")
        assert (change.outcome, change.pk) == ("created", profile.pk)

    def test_update_creates_when_there_is_none_and_updates_when_there_is(self) -> None:
        author = Author.objects.create(name="Ursula")
        spec = {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}

        created = update_from_input(author, {"profile": {"bio": "first"}}, relations=spec)
        profile = Profile.objects.get()
        assert created.get_relation_change("profile").outcome == "created"

        updated = update_from_input(author, {"profile": {"bio": "second"}}, relations=spec)
        profile.refresh_from_db()
        assert profile.bio == "second"
        assert Profile.objects.count() == 1
        change = updated.get_relation_change("profile")
        assert (change.outcome, change.pk) == ("updated", profile.pk)

    def test_none_unlinks_a_nullable_row(self) -> None:
        author = Author.objects.create(name="Ursula")
        profile = Profile.objects.create(author=author, bio="b")
        result = update_from_input(
            author,
            {"profile": None},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )
        profile.refresh_from_db()
        assert profile.author_id is None
        change = result.get_relation_change("profile")
        assert (change.outcome, change.pk) == ("unlinked", profile.pk)

    def test_none_deletes_a_non_nullable_row(self) -> None:
        catalog = Catalog.objects.create(name="c")
        cover = Cover.objects.create(catalog=catalog, image="i")
        result = update_from_input(
            catalog,
            {"cover": None},
            relations={"cover": ReverseOneToOneSpec(model=Cover, fk="catalog")},
        )
        assert not Cover.objects.filter(pk=cover.pk).exists()
        change = result.get_relation_change("cover")
        assert (change.outcome, change.pk) == ("deleted", cover.pk)

    def test_none_against_an_empty_relation_is_untouched(self) -> None:
        author = Author.objects.create(name="Ursula")
        result = update_from_input(
            author,
            {"profile": None},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )
        assert result.get_relation_change("profile").outcome == "untouched"

    def test_omitting_the_relation_touches_nothing(self) -> None:
        author = Author.objects.create(name="Ursula")
        profile = Profile.objects.create(author=author, bio="b")
        result = update_from_input(
            author,
            {"name": "renamed"},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )
        profile.refresh_from_db()
        assert (profile.author_id, profile.bio) == (author.pk, "b")
        assert result.get_relation_change("profile").outcome == "untouched"

    def test_a_delete_service_reports_removed(self) -> None:
        author = Author.objects.create(name="Ursula")
        profile = Profile.objects.create(author=author, bio="b")
        seen: dict[str, Any] = {}

        def archive(**pool: Any) -> None:
            seen.update(pool)

        result = update_from_input(
            author,
            {"profile": None},
            relations={
                "profile": ReverseOneToOneSpec(model=Profile, fk="author", delete_service=archive)
            },
            context={"user": "caller"},
        )
        assert (seen["instance"], seen["parent"], seen["user"]) == (profile, author, "caller")
        profile.refresh_from_db()
        assert profile.author_id == author.pk  # the service, not the unlink rule, ran
        change = result.get_relation_change("profile")
        assert (change.outcome, change.pk) == ("removed", profile.pk)

    def test_a_create_service_gets_the_parent_and_the_linked_data(self) -> None:
        seen: dict[str, Any] = {}

        def create_profile(**pool: Any) -> Profile:
            seen.update(pool)
            return Profile.objects.create(**pool["data"])

        result = create_from_input(
            Author,
            {"name": "Ursula", "profile": {"bio": "b"}},
            relations={
                "profile": ReverseOneToOneSpec(
                    model=Profile, fk="author", create_service=create_profile
                )
            },
        )
        assert seen["parent"] == result.instance
        assert seen["data"] == {"bio": "b", "author": result.instance}

    def test_an_update_service_returning_a_row_is_the_one_reported(self) -> None:
        author = Author.objects.create(name="Ursula")
        profile = Profile.objects.create(author=author, bio="b")
        replacement = Profile.objects.create(bio="replacement")

        def swap(*, instance: Profile) -> Profile:
            return replacement

        result = update_from_input(
            author,
            {"profile": {"bio": "ignored"}},
            relations={
                "profile": ReverseOneToOneSpec(model=Profile, fk="author", update_service=swap)
            },
        )
        assert result.get_relation_change("profile").pk == replacement.pk
        profile.refresh_from_db()
        assert profile.bio == "b"

    def test_the_row_carries_its_own_relations(self) -> None:
        tag = Tag.objects.create(name="t")
        result = create_from_input(
            Catalog,
            {"name": "c", "cover": {"image": "i"}, "sections": [{"title": "s"}]},
            relations={
                "cover": ReverseOneToOneSpec(model=Cover, fk="catalog"),
                "sections": ChildSpec(model=Section, fk="catalog", m2m=lambda row: {"tags": [tag]}),
            },
        )
        assert result.instance.cover.image == "i"
        assert result.instance.sections.get().tags.get() == tag

    def test_the_delete_cascade_removes_the_singular_row(self) -> None:
        # The parent owns this row, so the cascade removes it by the same rule
        # the write path applies to an explicit null -- and reports it as the
        # one-row change it is, rather than as a collection of one.
        catalog = Catalog.objects.create(name="c")
        cover = Cover.objects.create(catalog=catalog, image="i")

        collections, singular = delete_relations(
            catalog, {"cover": ReverseOneToOneSpec(model=Cover, fk="catalog")}
        )

        assert collections == ()
        assert (singular[0].relation, singular[0].outcome, singular[0].pk) == (
            "cover",
            "deleted",
            cover.pk,
        )
        assert not Cover.objects.exists()


@pytest.mark.django_db
class TestEveryKindTogether:
    def test_forward_then_parent_then_reverse(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula", "profile": {"bio": "b"}}},
            relations={
                "author": ForwardRelationSpec(
                    model=Author,
                    relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
                )
            },
        )
        author = Author.objects.get()
        assert result.instance.author == author
        assert Profile.objects.get().author == author

    def test_the_singular_and_collection_deltas_are_separate(self) -> None:
        result = create_from_input(
            Catalog,
            {"name": "c", "cover": {"image": "i"}, "notes": [{"body": "n"}]},
            relations={
                "cover": ReverseOneToOneSpec(model=Cover, fk="catalog"),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        assert [c.relation for c in result.relations] == ["cover"]
        assert [c.relation for c in result.children] == ["notes"]
        assert result.get_child_change("cover") is None
        assert result.get_relation_change("notes") is None


@pytest.mark.django_db(transaction=True)
class TestAsyncSingularRelations:
    async def test_forward_create_and_scoped_update(self) -> None:
        created = await acreate_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author)},
        )
        author = await Author.objects.aget()
        assert created.get_relation_change("author").pk == author.pk

        def owned_by(*, user: str) -> Any:
            return Author.objects.filter(name=user)

        updated = await acreate_from_input(
            Post,
            {"title": "t2", "author": {"pk": author.pk, "name": "renamed"}},
            relations={"author": ForwardRelationSpec(model=Author, scope=owned_by)},
            context={"user": "Ursula"},
        )
        assert updated.get_relation_change("author").outcome == "updated"
        assert await Author.objects.acount() == 1

    async def test_forward_clear_and_omit(self) -> None:
        author = await Author.objects.acreate(name="Ursula")
        post = await Post.objects.acreate(title="t", author=author)
        cleared = await aupdate_from_input(
            post, {"author": None}, relations={"author": ForwardRelationSpec(model=Author)}
        )
        assert cleared.get_relation_change("author").outcome == "cleared"
        await post.arefresh_from_db()
        assert post.author_id is None

        untouched = await aupdate_from_input(
            post, {"title": "t2"}, relations={"author": ForwardRelationSpec(model=Author)}
        )
        assert untouched.get_relation_change("author").outcome == "untouched"

    async def test_forward_unscoped_match_key_raises(self) -> None:
        author = await Author.objects.acreate(name="Ursula")
        with pytest.raises(ImproperlyConfigured, match=r"relations\['author'\]"):
            await acreate_from_input(
                Post,
                {"title": "t", "author": {"pk": author.pk}},
                relations={"author": ForwardRelationSpec(model=Author)},
            )

    async def test_forward_scoped_miss_is_refused_not_created(self) -> None:
        theirs = await Author.objects.acreate(name="theirs")
        with pytest.raises(ServiceValidationError):
            await acreate_from_input(
                Post,
                {"title": "t", "author": {"pk": theirs.pk, "name": "renamed"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, scope=Author.objects.filter(name="mine")
                    )
                },
            )
        await theirs.arefresh_from_db()
        assert theirs.name == "theirs"

    async def test_the_delete_cascade_removes_the_singular_row(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        cover = await Cover.objects.acreate(catalog=catalog, image="i")

        _, singular = await adelete_relations(
            catalog, {"cover": ReverseOneToOneSpec(model=Cover, fk="catalog")}
        )

        assert (singular[0].outcome, singular[0].pk) == ("deleted", cover.pk)
        assert not await Cover.objects.aexists()

    async def test_forward_create_service(self) -> None:
        async def create_author(*, data: Any) -> Author:
            return await Author.objects.acreate(name=data["name"].upper())

        result = await acreate_from_input(
            Post,
            {"title": "t", "author": {"name": "Ursula"}},
            relations={"author": ForwardRelationSpec(model=Author, create_service=create_author)},
        )
        assert (await Author.objects.aget()).name == "URSULA"
        assert result.get_relation_change("author").outcome == "created"

    async def test_reverse_create_update_and_unlink(self) -> None:
        spec = {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}
        created = await acreate_from_input(
            Author, {"name": "Ursula", "profile": {"bio": "b"}}, relations=spec
        )
        author = created.instance
        profile = await Profile.objects.aget()
        assert created.get_relation_change("profile").pk == profile.pk

        updated = await aupdate_from_input(author, {"profile": {"bio": "b2"}}, relations=spec)
        assert updated.get_relation_change("profile").outcome == "updated"
        await profile.arefresh_from_db()
        assert profile.bio == "b2"

        unlinked = await aupdate_from_input(author, {"profile": None}, relations=spec)
        assert unlinked.get_relation_change("profile").outcome == "unlinked"
        await profile.arefresh_from_db()
        assert profile.author_id is None

    async def test_reverse_none_deletes_and_then_is_untouched(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        cover = await Cover.objects.acreate(catalog=catalog, image="i")
        spec = {"cover": ReverseOneToOneSpec(model=Cover, fk="catalog")}

        deleted = await aupdate_from_input(catalog, {"cover": None}, relations=spec)
        assert deleted.get_relation_change("cover") == deleted.relations[0]
        assert (deleted.relations[0].outcome, deleted.relations[0].pk) == ("deleted", cover.pk)

        again = await aupdate_from_input(catalog, {"cover": None}, relations=spec)
        assert again.get_relation_change("cover").outcome == "untouched"

    async def test_reverse_omitted_and_services(self) -> None:
        author = await Author.objects.acreate(name="Ursula")
        profile = await Profile.objects.acreate(author=author, bio="b")
        seen: dict[str, Any] = {}

        async def archive(**pool: Any) -> None:
            seen.update(pool)

        omitted = await aupdate_from_input(
            author,
            {"name": "renamed"},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )
        assert omitted.get_relation_change("profile").outcome == "untouched"

        removed = await aupdate_from_input(
            author,
            {"profile": None},
            relations={
                "profile": ReverseOneToOneSpec(model=Profile, fk="author", delete_service=archive)
            },
        )
        assert seen["instance"] == profile
        assert removed.get_relation_change("profile").outcome == "removed"

    async def test_reverse_create_service_and_returned_row(self) -> None:
        async def create_profile(*, data: Any, parent: Any) -> Profile:
            assert data["author"] == parent
            return await Profile.objects.acreate(**data)

        created = await acreate_from_input(
            Author,
            {"name": "Ursula", "profile": {"bio": "b"}},
            relations={
                "profile": ReverseOneToOneSpec(
                    model=Profile, fk="author", create_service=create_profile
                )
            },
        )
        author = created.instance
        replacement = await Profile.objects.acreate(bio="replacement")

        async def swap(*, instance: Profile) -> Profile:
            return replacement

        updated = await aupdate_from_input(
            author,
            {"profile": {"bio": "ignored"}},
            relations={
                "profile": ReverseOneToOneSpec(model=Profile, fk="author", update_service=swap)
            },
        )
        assert updated.get_relation_change("profile").pk == replacement.pk
