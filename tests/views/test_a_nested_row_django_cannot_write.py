"""A nested row Django cannot write is the client's 400, naming the row.

A relation's row is written by the model's constructor, so a key the model
has no field for raises Django's ``TypeError`` there. The row's write
translates it into a refusal under the row's index, carrying Django's own
message, where 0.57.0 let the ``TypeError`` escape as a 500. The same key at
the top level is still the 500 it was: the top-level write is the
service's own, and its ``TypeError`` is never read as the client's mistake.
"""

from __future__ import annotations

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import ChildSpec, ServiceCreateView, ServiceSpec, create_model
from tests.testapp.models import Catalog, Section

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()


class _SectionIn(serializers.Serializer):
    title = serializers.CharField()
    name = serializers.CharField(required=False)


class _CatalogIn(serializers.Serializer):
    name = serializers.CharField()
    sections = _SectionIn(many=True, required=False)
    nonfield = serializers.CharField(required=False)


class _Create(ServiceCreateView):
    spec = ServiceSpec(
        service=create_model(
            Catalog, relations={"sections": ChildSpec(model=Section, fk="catalog")}
        ),
        input_serializer=_CatalogIn,
    )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"title": "t", "name": "zz"}, "Section() got unexpected keyword arguments: 'name'"),
    ],
    ids=["no-such-field"],
)
def test_the_row_is_refused_with_django_s_message(row: dict[str, str], message: str) -> None:
    body = {"name": "c", "sections": [{"title": "s"}, row]}

    response = _Create.as_view()(_factory.post("/", body, format="json"))

    assert response.status_code == 400
    assert response.data == {"sections": {1: {"non_field_errors": [message]}}}
    assert response.data["sections"][1]["non_field_errors"][0].code == "invalid"
    assert Catalog.objects.count() == 0


def test_the_same_key_at_the_top_level_is_still_a_server_error() -> None:
    with pytest.raises(TypeError, match="unexpected keyword arguments: 'nonfield'"):
        _Create.as_view()(_factory.post("/", {"name": "c", "nonfield": "x"}, format="json"))
