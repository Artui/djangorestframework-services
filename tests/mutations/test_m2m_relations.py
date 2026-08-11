"""Payload-driven many-to-many: write the targets, then the membership.

The distinction this kind exists for: the mutation helpers' ``m2m=`` argument
*assigns* rows that already exist, and a ``ManyToManySpec`` *writes* the rows
from the payload and then links them. Both ship, neither replaces the other,
and a relation named by both is refused.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ManyToManySpec,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from tests.testapp.models import Post, Tag

_TAGS = {"tags": ManyToManySpec(model=Tag)}


@pytest.mark.django_db
class TestTheTargetsAreWrittenNotAssigned:
    def test_create_writes_each_target_then_links_it(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "tags": [{"name": "django"}, {"name": "orm"}]},
            relations=_TAGS,
        )

        assert sorted(Tag.objects.values_list("name", flat=True)) == ["django", "orm"]
        assert result.instance.tags.count() == 2
        delta = result.get_child_change("tags")
        assert set(delta.created) == set(Tag.objects.values_list("pk", flat=True))
        assert (delta.updated, delta.unlinked, delta.deleted) == ((), (), ())

    def test_a_scoped_match_updates_the_target_instead_of_creating_one(self) -> None:
        tag = Tag.objects.create(name="old")
        result = create_from_input(
            Post,
            {"title": "t", "tags": [{"pk": tag.pk, "name": "new"}]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        tag.refresh_from_db()
        assert tag.name == "new"
        assert Tag.objects.count() == 1
        assert result.get_child_change("tags").updated == (tag.pk,)
        assert result.instance.tags.get() == tag

    def test_a_relation_the_input_omits_is_untouched(self) -> None:
        tag = Tag.objects.create(name="keep")
        post = Post.objects.create(title="t")
        post.tags.add(tag)

        result = update_from_input(post, {"title": "renamed"}, relations=_TAGS)

        assert list(post.tags.all()) == [tag]
        assert not result.get_child_change("tags")

    def test_the_target_carries_its_own_row_shaping(self) -> None:
        create_from_input(
            Post,
            {"title": "t", "tags": [{"label": "django", "junk": 1}]},
            relations={
                "tags": ManyToManySpec(
                    model=Tag, field_map={"label": "name"}, exclude_fields=["junk"]
                )
            },
        )

        assert Tag.objects.get().name == "django"


@pytest.mark.django_db
class TestModeDecidesWhatHappensToTheMembersLeftOut:
    def test_replace_drops_the_members_the_payload_omits(self) -> None:
        post = Post.objects.create(title="t")
        kept = Tag.objects.create(name="kept")
        dropped = Tag.objects.create(name="dropped")
        post.tags.set([kept, dropped])

        result = update_from_input(
            post,
            {"tags": [{"pk": kept.pk}]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        assert list(post.tags.all()) == [kept]
        delta = result.get_child_change("tags")
        assert delta.unlinked == (dropped.pk,)
        # A dropped target is unlinked, never deleted: the row is shared.
        assert delta.deleted == ()
        assert Tag.objects.filter(pk=dropped.pk).exists()

    def test_an_empty_list_empties_the_membership(self) -> None:
        post = Post.objects.create(title="t")
        tag = Tag.objects.create(name="t")
        post.tags.add(tag)

        result = update_from_input(post, {"tags": []}, relations=_TAGS)

        assert post.tags.count() == 0
        assert result.get_child_change("tags").unlinked == (tag.pk,)
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_merge_keeps_what_the_payload_leaves_out(self) -> None:
        post = Post.objects.create(title="t")
        existing = Tag.objects.create(name="existing")
        post.tags.add(existing)

        result = update_from_input(
            post,
            {"tags": [{"name": "added"}]},
            relations={"tags": ManyToManySpec(model=Tag, mode="merge")},
        )

        assert sorted(post.tags.values_list("name", flat=True)) == ["added", "existing"]
        assert result.get_child_change("tags").unlinked == ()

    def test_an_unknown_mode_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="ManyToManySpec.mode must be one of"):
            ManyToManySpec(model=Tag, mode="upsert")


@pytest.mark.django_db
class TestTheReverseSideIsTheSameCodePath:
    def test_a_reverse_accessor_writes_its_targets_too(self) -> None:
        # ``Post.tags`` declares the field; ``Tag.posts`` is the reverse
        # accessor. Django hands back the same related manager, so the loop
        # never needs to know which side it is on.
        result = create_from_input(
            Tag,
            {"name": "django", "posts": [{"title": "first"}, {"title": "second"}]},
            relations={"posts": ManyToManySpec(model=Post)},
        )

        assert sorted(result.instance.posts.values_list("title", flat=True)) == [
            "first",
            "second",
        ]
        assert len(result.get_child_change("posts").created) == 2


@pytest.mark.django_db
class TestScopeIsWhatMakesMatchingSafe:
    def test_an_unscoped_spec_meeting_a_match_key_is_a_misconfiguration(self) -> None:
        tag = Tag.objects.create(name="theirs")

        with pytest.raises(ImproperlyConfigured) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "tags": [{"pk": tag.pk, "name": "pwned"}]},
                relations=_TAGS,
            )

        message = str(excinfo.value)
        assert "relations['tags']" in message
        assert "ManyToManySpec" in message
        assert "scope=" in message
        tag.refresh_from_db()
        assert tag.name == "theirs"

    def test_a_key_outside_the_scope_is_refused_rather_than_created(self) -> None:
        theirs = Tag.objects.create(name="theirs")

        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "tags": [{"pk": theirs.pk, "name": "pwned"}]},
                relations={
                    "tags": ManyToManySpec(model=Tag, scope=Tag.objects.filter(name="mine"))
                },
            )

        assert "outside the scope" in excinfo.value.detail["tags"][0]
        theirs.refresh_from_db()
        assert theirs.name == "theirs"

    def test_the_scope_may_be_a_callable_resolved_from_the_context(self) -> None:
        mine = Tag.objects.create(name="mine")
        Tag.objects.create(name="theirs")

        create_from_input(
            Post,
            {"title": "t", "tags": [{"pk": mine.pk, "name": "renamed"}]},
            relations={
                "tags": ManyToManySpec(
                    model=Tag, scope=lambda owner: Tag.objects.filter(name=owner)
                )
            },
            context={"owner": "mine"},
        )

        mine.refresh_from_db()
        assert mine.name == "renamed"


@pytest.mark.django_db
class TestAM2MTargetCannotBeCreatedUnderAForeignPrimaryKey:
    """A target built from a nested payload is a create like any other, and
    ``Tag(pk=7, name="x").save()`` overwrites tag 7 rather than creating one.

    The way in is a spec matching on a natural key: the match only runs when
    the payload carries *that* key, so a payload carrying only a primary key
    walks past it into the create with the key still on board.
    """

    def test_a_primary_key_the_match_never_looked_at_is_refused(self) -> None:
        theirs = Tag.objects.create(name="theirs")

        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "tags": [{"pk": theirs.pk}]},
                relations={"tags": ManyToManySpec(model=Tag, match_key="name")},
            )

        assert "tags" in excinfo.value.detail
        theirs.refresh_from_db()
        assert theirs.name == "theirs"
        assert Tag.objects.count() == 1

    def test_the_target_service_is_never_handed_the_key(self) -> None:
        theirs = Tag.objects.create(name="theirs")
        seen: list[dict[str, Any]] = []

        def create_tag(*, data: dict[str, Any]) -> Tag:
            seen.append(dict(data))
            return Tag.objects.create(name=data.get("name", ""))

        with pytest.raises(ServiceValidationError):
            create_from_input(
                Post,
                {"title": "t", "tags": [{"pk": theirs.pk}]},
                relations={
                    "tags": ManyToManySpec(model=Tag, match_key="name", create_service=create_tag)
                },
            )

        assert seen == [], "the service was reached with a foreign primary key"
        assert Tag.objects.count() == 1


@pytest.mark.django_db
class TestAssignmentAndPayloadWritingCoexist:
    def test_the_m2m_argument_still_assigns_existing_rows(self) -> None:
        tag = Tag.objects.create(name="t")

        result = create_from_input(Post, {"title": "t"}, m2m={"tags": [tag]})

        assert list(result.instance.tags.all()) == [tag]

    def test_a_different_relation_may_be_written_from_the_payload(self) -> None:
        # One relation assigned, another written -- the two arguments are not
        # in competition, only a shared *name* is.
        assigned = Tag.objects.create(name="assigned")

        result = create_from_input(
            Tag,
            {"name": "parent", "posts": [{"title": "written"}]},
            m2m={"sections": []},
            relations={"posts": ManyToManySpec(model=Post)},
        )

        assert result.instance.posts.get().title == "written"
        assert Tag.objects.filter(pk=assigned.pk).exists()

    def test_a_relation_named_by_both_is_refused(self) -> None:
        tag = Tag.objects.create(name="t")

        with pytest.raises(ImproperlyConfigured) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "tags": [{"name": "written"}]},
                m2m={"tags": [tag]},
                relations=_TAGS,
            )

        message = str(excinfo.value)
        assert "'tags' is declared both in m2m= and as a relation" in message
        assert "Assign it or write it, not both" in message

    def test_the_refusal_covers_the_update_path_too(self) -> None:
        post = Post.objects.create(title="t")

        with pytest.raises(ImproperlyConfigured, match="declared both in m2m="):
            update_from_input(post, {"tags": []}, m2m={"tags": []}, relations=_TAGS)


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathWritesTheSameWay:
    async def test_create_writes_the_targets_and_links_them(self) -> None:
        result = await acreate_from_input(
            Post,
            {"title": "t", "tags": [{"name": "django"}]},
            relations=_TAGS,
        )

        assert await result.instance.tags.acount() == 1
        assert len(result.get_child_change("tags").created) == 1

    async def test_replace_drops_the_members_left_out(self) -> None:
        post = await Post.objects.acreate(title="t")
        kept = await Tag.objects.acreate(name="kept")
        dropped = await Tag.objects.acreate(name="dropped")
        await post.tags.aset([kept, dropped])

        result = await aupdate_from_input(
            post,
            {"tags": [{"pk": kept.pk}]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        delta = result.get_child_change("tags")
        assert (delta.updated, delta.unlinked) == ((kept.pk,), (dropped.pk,))
        assert await post.tags.acount() == 1
        assert await Tag.objects.filter(pk=dropped.pk).aexists()

    async def test_merge_adds_without_dropping(self) -> None:
        post = await Post.objects.acreate(title="t")
        existing = await Tag.objects.acreate(name="existing")
        await post.tags.aadd(existing)

        await aupdate_from_input(
            post,
            {"tags": [{"name": "added"}]},
            relations={"tags": ManyToManySpec(model=Tag, mode="merge")},
        )

        assert await post.tags.acount() == 2

    async def test_an_omitted_relation_is_untouched(self) -> None:
        post = await Post.objects.acreate(title="t")
        tag = await Tag.objects.acreate(name="keep")
        await post.tags.aadd(tag)

        result = await aupdate_from_input(post, {"title": "renamed"}, relations=_TAGS)

        assert await post.tags.acount() == 1
        assert not result.get_child_change("tags")

    async def test_an_unscoped_match_key_is_refused_here_too(self) -> None:
        tag = await Tag.objects.acreate(name="theirs")

        with pytest.raises(ImproperlyConfigured, match="scope="):
            await acreate_from_input(
                Post,
                {"title": "t", "tags": [{"pk": tag.pk}]},
                relations=_TAGS,
            )

    async def test_a_key_outside_the_scope_is_refused_here_too(self) -> None:
        theirs = await Tag.objects.acreate(name="theirs")

        with pytest.raises(ServiceValidationError) as excinfo:
            await acreate_from_input(
                Post,
                {"title": "t", "tags": [{"pk": theirs.pk}]},
                relations={
                    "tags": ManyToManySpec(model=Tag, scope=Tag.objects.filter(name="mine"))
                },
            )

        assert "outside the scope" in excinfo.value.detail["tags"][0]

    async def test_a_foreign_primary_key_is_refused_here_too(self) -> None:
        theirs = await Tag.objects.acreate(name="theirs")

        with pytest.raises(ServiceValidationError):
            await acreate_from_input(
                Post,
                {"title": "t", "tags": [{"pk": theirs.pk}]},
                relations={"tags": ManyToManySpec(model=Tag, match_key="name")},
            )

        refreshed = await Tag.objects.aget(pk=theirs.pk)
        assert refreshed.name == "theirs"

    async def test_a_target_service_and_row_shaping_reach_the_async_loop(self) -> None:
        async def create_tag(*, data: dict[str, Any], parent: Post) -> Tag:
            return await Tag.objects.acreate(name=f"{data['name']}-{parent.title}")

        await acreate_from_input(
            Post,
            {"title": "post", "tags": [{"name": "django"}]},
            relations={"tags": ManyToManySpec(model=Tag, create_service=create_tag)},
        )

        assert await Tag.objects.filter(name="django-post").aexists()


@pytest.mark.django_db
class TestATargetServiceOwnsTheRow:
    def test_the_create_slot_replaces_the_helper_call(self) -> None:
        def create_tag(*, data: dict[str, Any], parent: Post) -> Tag:
            return Tag.objects.create(name=f"{data['name']}-{parent.title}")

        result = create_from_input(
            Post,
            {"title": "post", "tags": [{"name": "django"}]},
            relations={"tags": ManyToManySpec(model=Tag, create_service=create_tag)},
        )

        assert result.instance.tags.get().name == "django-post"

    def test_the_update_slot_honours_the_none_return(self) -> None:
        tag = Tag.objects.create(name="old")
        post = Post.objects.create(title="t")

        def rename(*, instance: Tag, data: dict[str, Any]) -> None:
            instance.name = data["name"]
            instance.save(update_fields=["name"])
            return None

        result = update_from_input(
            post,
            {"tags": [{"pk": tag.pk, "name": "new"}]},
            relations={
                "tags": ManyToManySpec(model=Tag, scope=Tag.objects.all(), update_service=rename)
            },
        )

        tag.refresh_from_db()
        assert tag.name == "new"
        assert result.get_child_change("tags").updated == (tag.pk,)
        assert list(post.tags.all()) == [tag]

    def test_a_slot_beside_row_shaping_is_refused_at_construction(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="ManyToManySpec: create_service"):
            ManyToManySpec(model=Tag, create_service=lambda **_: None, field_map={"a": "b"})
