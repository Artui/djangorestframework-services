"""A matched row's primary key shapes nothing -- it is dropped from the write.

The nested-write specs have two readers of one payload. ``field_map`` and
``exclude_fields`` configure the row's **write**; matching, the primary-key
guard and the parent link all read the row exactly as it arrived. Where the
match key *is* the primary key those two readings collide: matching resolves
``pk="5"`` against pk ``5`` because the ORM coerces the string, then ``"5" != 5``
in Python, so ``pk`` reaches ``update_fields`` and Django refuses to update a
primary key -- a 500 for a payload the library had already matched.

Every kind is covered here even though all five share the same forwarding line,
which is exactly why: one kind exercises that line and satisfies the coverage
gate while the other four go untested, and the kinds do *not* behave alike. The
scoped kinds match through the ORM and reach the crash; the owned kinds match
through a dict built off the parent's manager, which does not coerce, so a
string key misses, falls through to create, and is refused cleanly.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    ReverseOneToOneSpec,
    ServiceValidationError,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from tests.testapp.models import Attachment, Author, Catalog, Post, Profile, Section, Tag


@pytest.mark.django_db
class TestAMatchedRowMayCarryItsOwnKey:
    """One case per kind: match on the key, then write through the same row."""

    def test_forward_relation(self) -> None:
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t", author=Author.objects.create(name="other"))

        update_from_input(
            post,
            {"author": {"pk": str(author.pk), "name": "renamed"}},
            relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
        )

        author.refresh_from_db()
        assert author.name == "renamed"

    def test_many_to_many(self) -> None:
        tag = Tag.objects.create(name="django")
        post = Post.objects.create(title="t")

        update_from_input(
            post,
            {"tags": [{"pk": str(tag.pk), "name": "renamed"}]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        tag.refresh_from_db()
        assert tag.name == "renamed"
        assert list(post.tags.all()) == [tag]

    def test_reverse_foreign_key_child(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")

        update_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "renamed"}]},
            children={"sections": ChildSpec(model=Section, fk="catalog")},
        )

        section.refresh_from_db()
        assert section.title == "renamed"

    def test_generic_relation(self) -> None:
        catalog = Catalog.objects.create(name="c")
        create_from_input(
            Catalog,
            {"name": "c2", "attachments": [{"label": "a"}]},
            relations={"attachments": GenericRelationSpec(model=Attachment)},
        )
        attachment = Attachment.objects.create(owner=catalog, label="a")

        update_from_input(
            catalog,
            {"attachments": [{"pk": attachment.pk, "label": "renamed"}]},
            relations={"attachments": GenericRelationSpec(model=Attachment)},
        )

        attachment.refresh_from_db()
        assert attachment.label == "renamed"

    def test_reverse_one_to_one(self) -> None:
        author = Author.objects.create(name="Ursula")
        profile = Profile.objects.create(author=author, bio="b")

        update_from_input(
            author,
            {"profile": {"pk": profile.pk, "bio": "renamed"}},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        )

        profile.refresh_from_db()
        assert profile.bio == "renamed"


@pytest.mark.django_db(transaction=True)
class TestTheAsyncWritersAgree:
    async def test_a_matched_row_may_carry_its_own_key(self) -> None:
        author = await Author.objects.acreate(name="Ursula")
        post = await Post.objects.acreate(title="t")

        await aupdate_from_input(
            post,
            {"author": {"pk": str(author.pk), "name": "renamed"}},
            relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
        )

        await author.arefresh_from_db()
        assert author.name == "renamed"

    async def test_a_matched_rows_write_fails_as_that_row(self) -> None:
        author = await Author.objects.acreate(name="Ursula")
        post = await Post.objects.acreate(title="t", author=author)

        with pytest.raises(ServiceValidationError) as excinfo:
            await aupdate_from_input(
                author,
                {"posts": [{"pk": post.pk, "views": "not-a-number"}]},
                children={"posts": ChildSpec(model=Post, fk="author")},
            )

        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        assert "not-a-number" in str(detail["posts"][0])

    async def test_the_create_path_still_refuses_an_unmatched_key(self) -> None:
        with pytest.raises(ServiceValidationError):
            await acreate_from_input(
                Post,
                {"title": "t", "author": {"pk": 4242, "name": "x"}},
                relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
            )


@pytest.mark.django_db
class TestOnlyTheKeyIsDropped:
    def test_a_natural_match_key_still_renames_the_row_it_matched(self) -> None:
        # The narrow reading: an identifier is dropped, a description is not.
        # ``title`` both finds the row and says what it should be called.
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="original")

        update_from_input(
            catalog,
            {"sections": [{"title": "original"}, {"title": "second"}]},
            children={"sections": ChildSpec(model=Section, fk="catalog", match_key="title")},
        )

        section.refresh_from_db()
        assert section.title == "original"
        assert Section.objects.filter(catalog=catalog).count() == 2

    def test_the_idiom_it_retires_is_still_harmless(self) -> None:
        # ``exclude_fields=[match_key]`` is what a consumer wrote by hand before
        # the write dropped the key itself. Saying it twice changes nothing.
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t")

        update_from_input(
            post,
            {"author": {"pk": str(author.pk), "name": "renamed"}},
            relations={
                "author": ForwardRelationSpec(
                    model=Author, scope=Author.objects.all(), exclude_fields=["pk"]
                )
            },
        )

        author.refresh_from_db()
        assert author.name == "renamed"

    def test_an_unmatched_key_is_still_refused_rather_than_dropped(self) -> None:
        # Dropping it on the *create* path would write a row under a key the
        # caller never chose, so the guard that refuses it is untouched. An
        # owned kind is where that guard shows: a miss there falls through to
        # create, where a scoped kind fails earlier in the scope lookup.
        catalog = Catalog.objects.create(name="c")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"sections": [{"pk": 4242, "title": "x"}]},
                children={"sections": ChildSpec(model=Section, fk="catalog")},
            )

        assert "did not match" in str(excinfo.value.detail)

    def test_a_key_the_row_never_sent_is_not_invented(self) -> None:
        # Nothing is read off the instance to stand in for the absent key, so
        # the row is a create and the row it used to point at is untouched.
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t", author=author)

        update_from_input(
            post,
            {"author": {"name": "second"}},
            relations={"author": ForwardRelationSpec(model=Author)},
        )

        author.refresh_from_db()
        assert author.name == "Ursula"
        post.refresh_from_db()
        assert post.author is not None
        assert post.author.name == "second"


@pytest.mark.django_db
class TestAFieldMapOntoTheKeyIsRefusedWhereItWouldBeUnreachable:
    """The matcher does not apply ``field_map``; the primary-key guard does."""

    @pytest.mark.parametrize(
        ("spec", "kwargs"),
        [
            (ChildSpec, {"model": Section, "fk": "catalog"}),
            (ForwardRelationSpec, {"model": Author}),
            (ManyToManySpec, {"model": Tag}),
            (GenericRelationSpec, {"model": Attachment}),
        ],
    )
    def test_every_kind_that_matches_by_key_refuses_it(
        self, spec: type, kwargs: dict[str, object]
    ) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            spec(field_map={"ident": "pk"}, **kwargs)

        assert "onto the primary key" in str(excinfo.value)

    def test_a_natural_match_key_leaves_the_same_mapping_usable(self) -> None:
        # ``ident`` is then an alias the guard reads and the matcher never
        # needed: the row matches on its title and the alias blocks creates.
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")

        update_from_input(
            catalog,
            {"sections": [{"title": "s", "ident": section.pk}]},
            children={
                "sections": ChildSpec(
                    model=Section, fk="catalog", match_key="title", field_map={"ident": "pk"}
                )
            },
        )

        assert Section.objects.filter(catalog=catalog).count() == 1

    def test_the_reverse_one_to_one_has_no_match_key_to_collide_with(self) -> None:
        # It finds its row through ``fk``, so nothing reads a key off the
        # payload and there is no unreachable combination to refuse.
        ReverseOneToOneSpec(model=Profile, fk="author", field_map={"ident": "pk"})


@pytest.mark.django_db
class TestARowsWriteFailsAsThatRow:
    def test_a_django_value_error_is_named_rather_than_escaping(self) -> None:
        # The backstop. Django raises a bare ``ValueError`` -- no ``detail`` to
        # namespace -- when a row's own data cannot be written at all, and
        # untranslated it leaves the service as a 500 naming no row.
        author = Author.objects.create(name="Ursula")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                author,
                {"posts": [{"title": "a"}, {"title": "b", "views": "not-a-number"}]},
                children={"posts": ChildSpec(model=Post, fk="author")},
            )

        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        # Reported against the second row, in a list as long as the incoming one.
        assert detail["posts"][0] == {}
        assert "not-a-number" in str(detail["posts"][1])

    def test_a_matched_row_fails_as_that_row_too(self) -> None:
        # The update writer has its own translation, and a matched row is the
        # only way to reach it -- the create path is a different call.
        author = Author.objects.create(name="Ursula")
        post = Post.objects.create(title="t", author=author)

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                author,
                {"posts": [{"pk": post.pk, "views": "not-a-number"}]},
                children={"posts": ChildSpec(model=Post, fk="author")},
            )

        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        assert "not-a-number" in str(detail["posts"][0])

    def test_a_row_services_own_value_error_is_left_alone(self) -> None:
        # Opaque caller code: reading its ``ValueError`` as a 400 would report
        # the author's own bug as the client's mistake.
        def update_service(*, instance: Section, data: dict[str, object]) -> Section:
            raise ValueError("a bug in my service")

        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")

        with pytest.raises(ValueError, match="a bug in my service"):
            update_from_input(
                catalog,
                {"sections": [{"pk": section.pk, "title": "renamed"}]},
                children={
                    "sections": ChildSpec(
                        model=Section, fk="catalog", update_service=update_service
                    )
                },
            )
