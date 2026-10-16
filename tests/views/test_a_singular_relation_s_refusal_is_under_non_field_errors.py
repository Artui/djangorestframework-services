"""A singular relation's refusal is a mapping under ``non_field_errors``.

The upgrade note states this shape, and each case here is one of its examples,
asserted as the exact payload and run twice: through ``create_from_input``
called directly, and through a live view whose service makes that call. The
shape is the kernel's nested-write code's, which drfs re-exports, so 0.57.0's
bare list or string under the relation's name (``{"author": ["A.", "B."]}``)
is gone on both paths at once.

A refusal that already named its own fields is left as it was, a Django
``ValidationError`` or a ``TypeError`` from a row service still propagates,
and a collection row keeps its index key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    ReverseOneToOneSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceValidationError,
    create_from_input,
)
from tests.testapp.models import Author, Catalog, Cover, Post, Profile, Section

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()

_UNMATCHED = (
    "references Profile [999], which this write did not match. Saving a new row "
    "under a primary key would overwrite the row that holds it, so it is refused. "
    "Send a row this relation may match, or omit the identifier to create a new one."
)


def _scope_miss(pk: int) -> str:
    return (
        f"No Author with pk={pk} is available to write: it does not exist, or it is "
        "outside the scope this relation may write. Omit the key to create a new one."
    )


def _refuses(detail: Any) -> Callable[..., None]:
    def service(**_: Any) -> None:
        raise ServiceValidationError(detail)

    return service


def _raises(error: Exception) -> Callable[..., None]:
    def service(**_: Any) -> None:
        raise error

    return service


def _forward(create_service: Callable[..., Any]) -> dict[str, ForwardRelationSpec]:
    return {"author": ForwardRelationSpec(model=Author, create_service=create_service)}


def _post_with_an_author(create_service: Callable[..., Any]) -> None:
    create_from_input(
        Post, {"title": "t", "author": {"name": "x"}}, relations=_forward(create_service)
    )


def _forward_scope_miss(payload: dict[str, Any]) -> None:
    create_from_input(
        Post,
        {"title": "t", "author": payload},
        relations={
            "author": ForwardRelationSpec(model=Author, scope=Author.objects.filter(name="mine"))
        },
    )


def _forward_one_to_one_scope_miss(payload: dict[str, Any]) -> None:
    create_from_input(
        Profile,
        {"bio": "b", "author": payload},
        relations={
            "author": ForwardRelationSpec(model=Author, scope=Author.objects.filter(name="mine"))
        },
    )


def _author_with_a_profile(create_service: Callable[..., Any]) -> None:
    create_from_input(
        Author,
        {"name": "a", "profile": {"bio": "..."}},
        relations={
            "profile": ReverseOneToOneSpec(
                model=Profile, fk="author", create_service=create_service
            )
        },
    )


def _a_post_author_whose_profile_refuses(create_service: Callable[..., Any]) -> None:
    create_from_input(
        Post,
        {"title": "t", "author": {"name": "x", "profile": {"bio": "b"}}},
        relations={
            "author": ForwardRelationSpec(
                model=Author,
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile, fk="author", create_service=create_service
                    )
                },
            )
        },
    )


def _a_section_whose_profile_refuses(create_service: Callable[..., Any]) -> None:
    # A singular relation inside one row of a collection: the index is the row's,
    # the relation name below it is the singular relation's.
    create_from_input(
        Catalog,
        {"name": "c", "sections": [{"title": "t", "profile": {"bio": "b"}}]},
        relations={
            "sections": ChildSpec(
                model=Section,
                fk="catalog",
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile, fk="author", create_service=create_service
                    )
                },
            )
        },
    )


# (call, expected payload). A call takes the body's ``payload``, which the two
# scope cases read as the row that names a primary key outside the scope.
_CASES: dict[str, tuple[Callable[[dict[str, Any]], None], Callable[[int], dict[str, Any]]]] = {
    "forward-foreign-key-scope-miss": (
        _forward_scope_miss,
        lambda pk: {"author": {"non_field_errors": [_scope_miss(pk)]}},
    ),
    "forward-one-to-one-scope-miss": (
        _forward_one_to_one_scope_miss,
        lambda pk: {"author": {"non_field_errors": [_scope_miss(pk)]}},
    ),
    "forward-row-service-raising-a-list": (
        lambda _: _post_with_an_author(_refuses(["A.", "B."])),
        lambda pk: {"author": {"non_field_errors": ["A.", "B."]}},
    ),
    "forward-row-service-raising-a-string": (
        lambda _: _post_with_an_author(_refuses("Rude.")),
        lambda pk: {"author": {"non_field_errors": ["Rude."]}},
    ),
    "forward-row-service-raising-drfs-validation-error": (
        lambda _: _post_with_an_author(_raises(ValidationError(["D1."]))),
        lambda pk: {"author": {"non_field_errors": ["D1."]}},
    ),
    "reverse-one-to-one-row-service-raising-a-list": (
        lambda _: _author_with_a_profile(_refuses(["L1.", "L2."])),
        lambda pk: {"profile": {"non_field_errors": ["L1.", "L2."]}},
    ),
    "reverse-one-to-one-unmatched-pk": (
        lambda _: create_from_input(
            Author,
            {"name": "a", "profile": {"pk": 999, "bio": "..."}},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author")},
        ),
        lambda pk: {"profile": {"non_field_errors": [_UNMATCHED]}},
    ),
    "a-singular-relation-inside-a-singular-relation": (
        lambda _: _a_post_author_whose_profile_refuses(_refuses(["N."])),
        lambda pk: {"author": {"profile": {"non_field_errors": ["N."]}}},
    ),
    "a-string-from-a-singular-relation-inside-a-singular-relation": (
        lambda _: _a_post_author_whose_profile_refuses(_refuses("S.")),
        lambda pk: {"author": {"profile": {"non_field_errors": ["S."]}}},
    ),
    "a-singular-relation-inside-a-collection-row": (
        lambda _: _a_section_whose_profile_refuses(_refuses(["RC."])),
        lambda pk: {"sections": {0: {"profile": {"non_field_errors": ["RC."]}}}},
    ),
    "a-collection-row-keeps-its-index": (
        lambda _: create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "t"}]},
            relations={
                "sections": ChildSpec(
                    model=Section, fk="catalog", create_service=_refuses("Too rude.")
                )
            },
        ),
        lambda pk: {"sections": {0: {"non_field_errors": ["Too rude."]}}},
    ),
    # A field map the row service named is not wrapped, and neither is a map
    # that already carries its own ``non_field_errors``.
    "a-field-map-is-not-wrapped": (
        lambda _: _post_with_an_author(_refuses({"name": ["Bad."]})),
        lambda pk: {"author": {"name": ["Bad."]}},
    ),
    "a-non-field-errors-map-is-not-wrapped-twice": (
        lambda _: _post_with_an_author(_refuses({"non_field_errors": ["Own."]})),
        lambda pk: {"author": {"non_field_errors": ["Own."]}},
    ),
}


class _Payload(serializers.Serializer):
    payload = serializers.JSONField()


def _view_running(call: Callable[[dict[str, Any]], None]) -> Any:
    def service(*, data: dict[str, Any]) -> None:
        call(data["payload"])

    class _Create(ServiceCreateView):
        spec = ServiceSpec(service=service, input_serializer=_Payload)

    return _Create.as_view()


def _body(pk: int) -> dict[str, Any]:
    return {"pk": pk, "name": "renamed"}


@pytest.mark.parametrize("name", list(_CASES))
def test_called_directly_the_refusal_is_the_stated_payload(name: str) -> None:
    call, expected = _CASES[name]
    theirs = Author.objects.create(name="theirs")

    with pytest.raises((ServiceValidationError, ValidationError)) as excinfo:
        call(_body(theirs.pk))

    assert excinfo.value.detail == expected(theirs.pk)
    # A service that reached for DRF's error keeps it, and the rest are drfs' own.
    drf_own = name == "forward-row-service-raising-drfs-validation-error"
    assert isinstance(excinfo.value, ServiceValidationError) is not drf_own


@pytest.mark.parametrize("name", list(_CASES))
def test_over_a_live_view_the_400_carries_the_stated_payload(name: str) -> None:
    call, expected = _CASES[name]
    theirs = Author.objects.create(name="theirs")

    response = _view_running(call)(_factory.post("/", {"payload": _body(theirs.pk)}, format="json"))

    assert response.status_code == 400
    assert response.data == expected(theirs.pk)


def test_the_wrapper_is_the_kernel_s_so_a_bare_list_is_never_answered() -> None:
    # The 0.57.0 shapes this replaces, pinned as absent: a client reading a
    # singular relation's refusal reads it under ``non_field_errors``.
    with pytest.raises(ServiceValidationError) as excinfo:
        _post_with_an_author(_refuses(["A.", "B."]))

    assert excinfo.value.detail != {"author": ["A.", "B."]}
    assert excinfo.value.detail["author"] == {"non_field_errors": ["A.", "B."]}


def test_a_second_reverse_one_to_one_wraps_the_same_way() -> None:
    with pytest.raises(ServiceValidationError) as excinfo:
        create_from_input(
            Catalog,
            {"name": "c", "cover": {"image": "i"}},
            relations={
                "cover": ReverseOneToOneSpec(
                    model=Cover, fk="catalog", create_service=_refuses(["C."])
                )
            },
        )

    assert excinfo.value.detail == {"cover": {"non_field_errors": ["C."]}}


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (DjangoValidationError("J1."), DjangoValidationError),
        (TypeError("T1."), TypeError),
    ],
    ids=["django-validation-error", "type-error"],
)
class TestARowServiceErrorThatIsNotDrfsStillPropagates:
    def test_called_directly(self, error: Exception, expected_type: type[Exception]) -> None:
        with pytest.raises(expected_type) as excinfo:
            _post_with_an_author(_raises(error))

        assert excinfo.value is error

    def test_over_a_live_view(self, error: Exception, expected_type: type[Exception]) -> None:
        view = _view_running(lambda _: _post_with_an_author(_raises(error)))

        with pytest.raises(expected_type) as excinfo:
            view(_factory.post("/", {"payload": {}}, format="json"))

        assert excinfo.value is error
