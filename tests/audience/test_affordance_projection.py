"""The ``affordances`` answers in rendered output, and the schema that describes them.

Asserted on the payload as a transport serves it -- rendered, then round-tripped
through JSON -- and validated against the schema generated from the same
declaration, because a schema that agrees only with itself proves nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import Q, QuerySet
from django.test.utils import CaptureQueriesContext
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services import (
    MARKING,
    Affordance,
    FieldMarking,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    arender_for_audience,
    arender_spec_output,
    capability_manifest,
    dispatch_spec,
    output_to_json_schema,
    render_for_audience,
    render_spec_output,
    spec_to_json_schema,
)
from rest_framework_services.types.audience_projection import AudienceProjection
from tests.testapp.models import Post

UNPUBLISHED = Affordance(
    code="already_published",
    reason="Went out in the 09:00 internal newsletter.",
    when=Q(published=False),
)
BOOKS_OPEN = Affordance(code="books_closed", reason="Ledger 4 is locked.", when=lambda: True)
PUBLISH = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED, BOOKS_OPEN])
EDIT = ServiceSpec(service=lambda: None)


class _PostOut(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()


class _MarkedPostOut(serializers.Serializer):
    id = serializers.IntegerField(style={MARKING: FieldMarking.handle()})
    title = serializers.CharField(style={MARKING: FieldMarking.hidden()})


class _ClashingOut(serializers.Serializer):
    id = serializers.IntegerField()
    affordances = serializers.CharField(default="mine")


def _posts() -> QuerySet[Post]:
    return Post.objects.order_by("id")


def _listing(**fields: Any) -> SelectorSpec[Any, Any]:
    fields.setdefault("selector", _posts)
    fields.setdefault("output_serializer", _PostOut)
    fields.setdefault("affordances", {"publish": PUBLISH, "edit": EDIT})
    return SelectorSpec(kind=SelectorKind.LIST, **fields)


def _rows(spec: SelectorSpec[Any, Any]) -> list[Post]:
    return list(dispatch_spec(spec, user=None, params={}).value)


def _wire(payload: Any) -> Any:
    return json.loads(json.dumps(payload))


@pytest.fixture
def two_posts(db: Any) -> tuple[Post, Post]:
    return Post.objects.create(title="draft"), Post.objects.create(title="out", published=True)


# --- render_spec_output ---------------------------------------------------------------


def test_each_rendered_row_carries_its_answers_as_served(two_posts: tuple[Post, Post]) -> None:
    draft, out = two_posts
    spec = _listing()

    payload = _wire(render_spec_output(spec, _rows(spec), many=True))

    assert payload == [
        {
            "id": draft.pk,
            "title": "draft",
            "affordances": {"publish": {"available": True}, "edit": {"available": True}},
        },
        {
            "id": out.pk,
            "title": "out",
            "affordances": {
                "publish": {
                    "available": False,
                    "code": "already_published",
                    "reason": "Went out in the 09:00 internal newsletter.",
                },
                "edit": {"available": True},
            },
        },
    ]


def test_the_first_unmet_condition_in_declaration_order_is_the_one_reported(
    two_posts: tuple[Post, Post],
) -> None:
    closed = Affordance(code="books_closed", reason="Closed.", when=lambda: False)
    both = ServiceSpec(service=lambda: None, affordances=[closed, UNPUBLISHED])
    spec = _listing(affordances={"publish": both})

    payload = render_spec_output(spec, _rows(spec), many=True)

    assert [row["affordances"]["publish"].get("code") for row in payload] == [
        "books_closed",
        "books_closed",
    ]


def test_a_single_row_renders_as_one_object(two_posts: tuple[Post, Post]) -> None:
    _draft, out = two_posts
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
    )
    row = dispatch_spec(spec, user=None, params={"pk": out.pk}).value

    assert render_spec_output(spec, row)["affordances"]["publish"]["code"] == "already_published"


def test_nothing_carries_no_answers(db: Any) -> None:
    """``None`` is not a row: it renders as the serializer renders it, with nothing
    to report about."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.none(),
        allow_none=True,
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
    )
    assert render_spec_output(spec, None) == _PostOut(None).data
    assert "affordances" not in render_for_audience(spec, None)


def test_a_mutation_renders_the_answers_of_its_output_selector_not_its_own(
    two_posts: tuple[Post, Post],
) -> None:
    """A ``ServiceSpec``'s own ``affordances`` are what it is checked against; only its
    output selector's are about the row it hands back."""
    draft, _out = two_posts
    checked_only = ServiceSpec(
        service=lambda *, instance: instance,
        affordances=[UNPUBLISHED],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, result: Post.objects.filter(pk=result.pk),
            output_serializer=_PostOut,
        ),
    )
    rendering = ServiceSpec(
        service=lambda *, instance: instance,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, result: Post.objects.filter(pk=result.pk),
            output_serializer=_PostOut,
            affordances={"publish": PUBLISH},
        ),
    )

    plain = dispatch_spec(checked_only, user=None, params={}, instance=draft).value
    assert "affordances" not in render_spec_output(checked_only, plain)
    annotated = dispatch_spec(rendering, user=None, params={}, instance=draft).value
    assert render_spec_output(rendering, annotated)["affordances"] == {
        "publish": {"available": True}
    }


def test_a_mutation_with_its_own_affordances_and_no_output_renders_its_value_untouched(
    db: Any,
) -> None:
    spec = ServiceSpec(service=lambda: None, affordances=[UNPUBLISHED])
    assert render_spec_output(spec, {"ok": True}) == {"ok": True}


def test_rendering_the_answers_costs_no_query(two_posts: tuple[Post, Post]) -> None:
    spec = _listing()
    rows = _rows(spec)

    with CaptureQueriesContext(connection) as ctx:
        render_spec_output(spec, rows, many=True)
    assert ctx.captured_queries == []


def test_rows_given_as_a_one_shot_iterable_are_walked_once(two_posts: tuple[Post, Post]) -> None:
    """The serializer and the answers both walk the rows; an iterator would be spent
    by the first."""
    spec = _listing()
    payload = render_spec_output(spec, iter(_rows(spec)), many=True)
    assert [row["title"] for row in payload] == ["draft", "out"]
    assert all("affordances" in row for row in payload)


def test_values_rows_carry_their_answers_as_keys(two_posts: tuple[Post, Post]) -> None:
    spec = _listing(selector=lambda: Post.objects.order_by("id").values())
    rows = list(dispatch_spec(spec, user=None, params={}).value)

    payload = render_spec_output(spec, rows, many=True)

    assert [row["affordances"]["publish"]["available"] for row in payload] == [True, False]


def test_declaring_nothing_renders_exactly_what_the_serializer_did(
    two_posts: tuple[Post, Post],
) -> None:
    spec = _listing(affordances=None)
    rows = _rows(spec)
    assert render_spec_output(spec, rows, many=True) == _PostOut(rows, many=True).data


def test_no_serializer_is_refused_rather_than_dropping_the_answers(
    two_posts: tuple[Post, Post],
) -> None:
    spec = _listing(output_serializer=None)
    with pytest.raises(ImproperlyConfigured, match="declares no output_serializer"):
        render_spec_output(spec, _rows(spec), many=True)


def test_a_serializer_field_named_affordances_is_refused(two_posts: tuple[Post, Post]) -> None:
    spec = _listing(output_serializer=_ClashingOut)
    with pytest.raises(ImproperlyConfigured, match="already has an 'affordances' key"):
        render_spec_output(spec, _rows(spec), many=True)


def test_a_row_the_selector_did_not_annotate_is_refused(two_posts: tuple[Post, Post]) -> None:
    with pytest.raises(ImproperlyConfigured, match="carries no 'affordance__publish__"):
        render_spec_output(_listing(), list(Post.objects.order_by("id")), many=True)


def test_a_non_object_item_is_refused(two_posts: tuple[Post, Post]) -> None:
    class _Scalar(serializers.Serializer):
        def to_representation(self, instance: Any) -> Any:
            return instance.title

    spec = _listing(output_serializer=_Scalar)
    with pytest.raises(ImproperlyConfigured, match="rendered str"):
        render_spec_output(spec, _rows(spec), many=True)


# --- the agent audience ----------------------------------------------------------------


def test_an_agent_reads_the_code_and_never_the_reason(two_posts: tuple[Post, Post]) -> None:
    spec = _listing()

    payload = _wire(render_for_audience(spec, _rows(spec), many=True))

    assert payload[1]["affordances"] == {
        "publish": {"available": False, "code": "already_published"},
        "edit": {"available": True},
    }
    assert "newsletter" not in json.dumps(payload)


def test_an_agent_single_row_loses_the_reason_too(two_posts: tuple[Post, Post]) -> None:
    _draft, out = two_posts
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
    )
    row = dispatch_spec(spec, user=None, params={"pk": out.pk}).value

    assert render_for_audience(spec, row)["affordances"] == {
        "publish": {"available": False, "code": "already_published"}
    }


def test_field_markings_and_the_answers_apply_together(two_posts: tuple[Post, Post]) -> None:
    spec = _listing(output_serializer=_MarkedPostOut)

    payload = render_for_audience(spec, _rows(spec), many=True)

    assert payload[1] == {
        "id": two_posts[1].pk,
        "affordances": {
            "publish": {"available": False, "code": "already_published"},
            "edit": {"available": True},
        },
    }


def test_declaring_nothing_leaves_the_agent_payload_as_it_was(
    two_posts: tuple[Post, Post],
) -> None:
    spec = _listing(affordances=None)
    rows = _rows(spec)
    assert render_for_audience(spec, rows, many=True) == render_spec_output(spec, rows, many=True)


@pytest.mark.django_db(transaction=True)
async def test_the_async_twins_render_the_same_answers() -> None:
    await Post.objects.acreate(title="draft")
    await Post.objects.acreate(title="out", published=True)
    spec = _listing()
    rows = await sync_to_async(_rows, thread_sensitive=True)(spec)

    served = await arender_spec_output(spec, rows, many=True)
    agent = await arender_for_audience(spec, rows, many=True)

    assert served[1]["affordances"]["publish"]["reason"] == UNPUBLISHED.reason
    assert agent[1]["affordances"]["publish"] == {"available": False, "code": "already_published"}


# --- the schema --------------------------------------------------------------------------

_ANSWERS_WITH_REASON = {
    "type": "object",
    "properties": {
        "publish": {
            "type": "object",
            "properties": {
                "available": {"type": "boolean"},
                "code": {"type": "string", "enum": ["already_published", "books_closed"]},
                "reason": {"type": "string"},
            },
            "required": ["available"],
        },
        "edit": {
            "type": "object",
            "properties": {"available": {"type": "boolean"}},
            "required": ["available"],
        },
    },
    "required": ["publish", "edit"],
}


def test_the_output_schema_declares_the_answers() -> None:
    assert spec_to_json_schema(_listing(), phase="output") == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "title": {"type": "string"},
                "affordances": _ANSWERS_WITH_REASON,
            },
            "required": ["id", "title", "affordances"],
        },
    }


def test_the_agent_schema_declares_no_reason() -> None:
    schema = output_to_json_schema(
        _PostOut,
        kind=SelectorKind.LIST,
        paginate=True,
        projection=AudienceProjection(),
        affordances={"publish": PUBLISH, "edit": EDIT},
    )
    assert schema is not None
    publish = schema["properties"]["items"]["items"]["properties"]["affordances"]["properties"][
        "publish"
    ]
    assert publish["properties"] == {
        "available": {"type": "boolean"},
        "code": {"type": "string", "enum": ["already_published", "books_closed"]},
    }


def test_a_mutations_output_schema_declares_its_output_selectors_answers() -> None:
    spec = ServiceSpec(
        service=lambda: None,
        affordances=[UNPUBLISHED],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=_PostOut, affordances={"edit": EDIT}
        ),
    )
    schema = spec_to_json_schema(spec, phase="output")
    assert schema is not None
    assert schema["properties"]["affordances"] == {
        "type": "object",
        "properties": {
            "edit": {
                "type": "object",
                "properties": {"available": {"type": "boolean"}},
                "required": ["available"],
            }
        },
        "required": ["edit"],
    }


def test_declaring_nothing_leaves_the_output_schema_as_it_was() -> None:
    assert spec_to_json_schema(_listing(affordances=None), phase="output") == output_to_json_schema(
        _PostOut, kind=SelectorKind.LIST
    )
    schema = spec_to_json_schema(_listing(affordances=None), phase="output")
    assert schema is not None
    assert "affordances" not in schema["items"]["properties"]


def test_every_served_payload_validates_against_its_own_schema(
    two_posts: tuple[Post, Post],
) -> None:
    spec = _listing()
    rows = _rows(spec)
    served_schema = spec_to_json_schema(spec, phase="output")
    agent_schema = output_to_json_schema(
        _PostOut,
        kind=SelectorKind.LIST,
        projection=AudienceProjection(),
        affordances=spec.affordances,
    )

    Draft202012Validator(served_schema).validate(_wire(render_spec_output(spec, rows, many=True)))
    Draft202012Validator(agent_schema).validate(_wire(render_for_audience(spec, rows, many=True)))


def test_a_code_outside_the_declaration_fails_the_schema(two_posts: tuple[Post, Post]) -> None:
    """The enum is what lets the validation above mean something."""
    spec = _listing()
    payload = _wire(render_spec_output(spec, _rows(spec), many=True))
    payload[1]["affordances"]["publish"]["code"] = "made_up"

    errors = list(
        Draft202012Validator(spec_to_json_schema(spec, phase="output")).iter_errors(payload)
    )
    assert errors


# --- the manifest ----------------------------------------------------------------------


def test_the_manifest_lists_the_codes_a_mutation_can_answer_with() -> None:
    registry = SpecRegistry()
    registry.register("publish_post", PUBLISH)
    registry.register("edit_post", EDIT)
    registry.register("list_posts", _listing())

    operations = {op["name"]: op for op in capability_manifest(registry)["operations"]}

    assert operations["publish_post"]["affordances"] == [
        {"code": "already_published", "scope": "row"},
        {"code": "books_closed", "scope": "operation"},
    ]
    assert operations["edit_post"]["affordances"] is None
    assert operations["list_posts"]["affordances"] is None
    # The query reports the same vocabulary where it is rendered: in its schema.
    item = operations["list_posts"]["output_schema"]["items"]
    assert item["properties"]["affordances"]["properties"]["publish"]["properties"]["code"][
        "enum"
    ] == [entry["code"] for entry in operations["publish_post"]["affordances"]]
    assert "reason" not in json.dumps(operations["publish_post"])
