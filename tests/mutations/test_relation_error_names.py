"""``error_name=`` decides what a refused relation is called on the way out.

The map key names a relation to whoever wrote the spec. It is not always what
the client called it: a serializer aliasing a nested field
(``writer = AuthorSerializer(source="author")``) hands the helpers a
``validated_data`` keyed by ``source``, so the relation must be declared as
``"author"`` -- and every refusal about it then names a field the request never
had. These assert the override reaches all three refusals the library can raise
about a relation, that it leaves the change carriers alone, and that omitting it
reports exactly what it reported before.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    ReverseOneToOneSpec,
    ServiceValidationError,
    acreate_from_input,
    create_from_input,
    update_from_input,
)
from tests.testapp.models import Attachment, Author, Catalog, Post, Profile, Section, Tag

_TOO_SHORT: dict[str, Any] = {"name": ["Too short."]}


def _refuses(**_: Any) -> None:
    """A row service that refuses whatever it is handed."""
    raise ServiceValidationError(_TOO_SHORT)


async def _arefuses(**_: Any) -> None:
    """The async twin of :func:`_refuses`."""
    raise ServiceValidationError(_TOO_SHORT)


@pytest.mark.django_db
class TestAServiceErrorTakesTheWireName:
    def test_a_forward_relation_reports_under_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                # What DRF hands over: keyed by ``source``, not by the field name.
                {"title": "t", "author": {"name": "x"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, create_service=_refuses, error_name="writer"
                    )
                },
            )

        assert excinfo.value.detail == {"writer": _TOO_SHORT}

    def test_a_collection_keeps_its_row_alignment(self) -> None:
        """The alias renames the key; the ``ListSerializer`` shape under it stands."""
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "ok"}, {"title": "bad"}]},
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        create_service=lambda **kwargs: (
                            Section.objects.create(**kwargs["data"])
                            if kwargs["data"]["title"] == "ok"
                            else _refuses()
                        ),
                        error_name="chapters",
                    )
                },
            )

        assert excinfo.value.detail == {"chapters": [{}, _TOO_SHORT]}

    def test_a_reverse_one_to_one_reports_under_the_alias(self) -> None:
        author = Author.objects.create(name="a")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                author,
                {"profile": {"bio": "b"}},
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile, fk="author", create_service=_refuses, error_name="about"
                    )
                },
            )

        assert excinfo.value.detail == {"about": _TOO_SHORT}

    def test_a_generic_relation_reports_under_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "attachments": [{"label": "a"}]},
                relations={
                    "attachments": GenericRelationSpec(
                        model=Attachment, create_service=_refuses, error_name="files"
                    )
                },
            )

        assert excinfo.value.detail == {"files": [_TOO_SHORT]}

    def test_a_many_to_many_reports_under_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "tags": [{"name": "x"}]},
                relations={
                    "tags": ManyToManySpec(model=Tag, create_service=_refuses, error_name="labels")
                },
            )

        assert excinfo.value.detail == {"labels": [_TOO_SHORT]}

    def test_drf_s_own_error_class_is_renamed_too(self) -> None:
        """The class is preserved on the way out, so the alias must reach both."""

        def refuse_with_drf(**_: Any) -> None:
            raise ValidationError(_TOO_SHORT)

        with pytest.raises(ValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"name": "x"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, create_service=refuse_with_drf, error_name="writer"
                    )
                },
            )

        assert "writer" in excinfo.value.detail


@pytest.mark.django_db
class TestTheLibrarySOwnRefusalsTakeItToo:
    def test_the_primary_key_guard_reports_under_the_alias(self) -> None:
        catalog = Catalog.objects.create(name="c")
        stranger = Section.objects.create(catalog=Catalog.objects.create(name="other"), title="s")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"sections": [{"pk": stranger.pk, "title": "mine"}]},
                children={
                    "sections": ChildSpec(model=Section, fk="catalog", error_name="chapters")
                },
            )

        assert set(excinfo.value.detail) == {"chapters"}

    def test_a_scope_miss_reports_under_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"pk": 999, "name": "x"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, scope=Author.objects.none(), error_name="writer"
                    )
                },
            )

        assert set(excinfo.value.detail) == {"writer"}

    def test_a_misconfiguration_still_quotes_the_map_key(self) -> None:
        """Author-facing, so it names the relation the way the spec declares it.

        ``error_name`` is what the *client* calls the relation. An unscoped spec
        handed a match key is nobody's fault but the spec author's, and pointing
        them at a key that does not appear in their ``relations=`` map would
        send them looking for a declaration that is not there.
        """
        with pytest.raises(Exception, match=r"relations\['author'\]"):
            create_from_input(
                Post,
                {"title": "t", "author": {"pk": 1, "name": "x"}},
                relations={"author": ForwardRelationSpec(model=Author, error_name="writer")},
            )


@pytest.mark.django_db
class TestWhatTheAliasDoesNotRename:
    def test_the_payload_is_still_read_from_the_map_key(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "author": {"name": "a"}},
            relations={"author": ForwardRelationSpec(model=Author, error_name="writer")},
        )

        assert result.instance.author == Author.objects.get()

    def test_the_change_carriers_still_label_the_map_key(self) -> None:
        """Server-side reporting speaks the spec author's vocabulary, not the wire's."""
        catalog = Catalog.objects.create(name="c")

        result = update_from_input(
            catalog,
            {"sections": [{"title": "s"}]},
            children={"sections": ChildSpec(model=Section, fk="catalog", error_name="chapters")},
        )

        assert [change.relation for change in result.children] == ["sections"]

    def test_omitting_it_reports_the_map_key(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"name": "x"}},
                relations={"author": ForwardRelationSpec(model=Author, create_service=_refuses)},
            )

        assert excinfo.value.detail == {"author": _TOO_SHORT}


@pytest.mark.django_db
class TestNamesStillNest:
    def test_a_grandchild_alias_nests_inside_its_parent_s(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "s", "tags": [{"name": "x"}]}]},
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        error_name="chapters",
                        relations={
                            "tags": ManyToManySpec(
                                model=Tag, create_service=_refuses, error_name="labels"
                            )
                        },
                    )
                },
            )

        assert excinfo.value.detail == {"chapters": [{"labels": [_TOO_SHORT]}]}


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathAgrees:
    async def test_an_async_row_error_takes_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await acreate_from_input(
                Post,
                {"title": "t", "author": {"name": "x"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, create_service=_arefuses, error_name="writer"
                    )
                },
            )

        assert excinfo.value.detail == {"writer": _TOO_SHORT}

    async def test_an_async_scope_miss_takes_the_alias(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await acreate_from_input(
                Post,
                {"title": "t", "author": {"pk": 999, "name": "x"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, scope=Author.objects.none(), error_name="writer"
                    )
                },
            )

        assert set(excinfo.value.detail) == {"writer"}
