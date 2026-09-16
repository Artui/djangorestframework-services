"""The ``affordances`` answers in rendered output, and the schema that describes them.

Asserted on the payload as a transport serves it -- rendered, then round-tripped
through JSON -- and validated against the schema generated from the same
declaration, because a schema that agrees only with itself proves nothing.
"""

from __future__ import annotations

import dataclasses
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
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    MARKING,
    Affordance,
    FieldMarking,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    arender_for_audience,
    arender_spec_output,
    audience_projection_for_spec,
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
    reason="This post is already published.",
    when=Q(published=False),
)
BOOKS_OPEN = Affordance(
    code="books_closed", reason="Publishing is paused until the books close.", when=lambda: True
)
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
                    "reason": "This post is already published.",
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
    """``None`` is not a row: it renders as ``None``, with nothing to report about."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.none(),
        allow_none=True,
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
    )
    assert render_spec_output(spec, None) is None
    assert render_for_audience(spec, None) is None


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


def test_a_list_selectors_instances_render_as_a_querysets_do(
    two_posts: tuple[Post, Post],
) -> None:
    from_queryset = _listing()
    from_list = _listing(selector=lambda: list(_posts()))

    for render in (render_spec_output, render_for_audience):
        assert render(from_list, _rows(from_list), many=True) == render(
            from_queryset, _rows(from_queryset), many=True
        )


def test_a_list_selectors_mappings_render_their_callable_answers(db: Any) -> None:
    spec = _listing(
        selector=lambda: [{"id": 1, "title": "computed"}],
        affordances={"edit": ServiceSpec(service=lambda: None, affordances=[BOOKS_OPEN])},
    )

    payload = _wire(render_spec_output(spec, _rows(spec), many=True))

    assert payload == [{"id": 1, "title": "computed", "affordances": {"edit": {"available": True}}}]


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


_REFUSED = {
    "available": False,
    "code": "already_published",
    "reason": "This post is already published.",
}


def test_an_agent_reads_the_same_answers_a_browser_does(two_posts: tuple[Post, Post]) -> None:
    """The reason is the sentence a model relays; the code is what it branches on.
    Both reach it, exactly as they reach a browser."""
    spec = _listing()
    rows = _rows(spec)

    agent = _wire(render_for_audience(spec, rows, many=True))
    browser = _wire(render_spec_output(spec, rows, many=True))

    assert agent[1]["affordances"] == {"publish": _REFUSED, "edit": {"available": True}}
    assert [row["affordances"] for row in agent] == [row["affordances"] for row in browser]


def test_an_agent_single_row_carries_the_reason_too(two_posts: tuple[Post, Post]) -> None:
    _draft, out = two_posts
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_PostOut,
        affordances={"publish": PUBLISH},
    )
    row = dispatch_spec(spec, user=None, params={"pk": out.pk}).value

    assert render_for_audience(spec, row)["affordances"] == {"publish": _REFUSED}


def test_field_markings_and_the_answers_apply_together(two_posts: tuple[Post, Post]) -> None:
    """Markings shape the serializer's fields and leave the answers whole."""
    spec = _listing(output_serializer=_MarkedPostOut)

    payload = render_for_audience(spec, _rows(spec), many=True)

    assert payload[1] == {
        "id": two_posts[1].pk,
        "affordances": {"publish": _REFUSED, "edit": {"available": True}},
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

    assert served[1]["affordances"]["publish"] == _REFUSED
    assert agent[1]["affordances"]["publish"] == _REFUSED


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


def test_the_agent_schema_declares_the_same_answers_reason_included() -> None:
    agent = output_to_json_schema(
        _PostOut,
        kind=SelectorKind.LIST,
        paginate=True,
        projection=AudienceProjection(),
        affordances={"publish": PUBLISH, "edit": EDIT},
    )
    assert agent is not None
    answers = agent["properties"]["items"]["items"]["properties"]["affordances"]

    assert answers == _ANSWERS_WITH_REASON


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
        {"code": "already_published", "reason": "This post is already published.", "scope": "row"},
        {
            "code": "books_closed",
            "reason": "Publishing is paused until the books close.",
            "scope": "operation",
        },
    ]
    assert operations["edit_post"]["affordances"] is None
    assert operations["list_posts"]["affordances"] is None
    # The query reports the same vocabulary where it is rendered: in its schema.
    item = operations["list_posts"]["output_schema"]["items"]
    assert item["properties"]["affordances"]["properties"]["publish"]["properties"]["code"][
        "enum"
    ] == [entry["code"] for entry in operations["publish_post"]["affordances"]]


# --- a row gone before its answers were asked ------------------------------------------

CLOSED = Affordance(
    code="books_closed", reason="Publishing is paused until the books close.", when=lambda: False
)


def _then_delete_the_first() -> list[Post]:
    rows = list(_posts())
    Post.objects.filter(pk=rows[0].pk).delete()
    return rows


def _vanishing(*affordances: Affordance) -> SelectorSpec[Any, Any]:
    return _listing(
        selector=_then_delete_the_first,
        affordances={"publish": ServiceSpec(service=lambda: None, affordances=list(affordances))},
    )


def test_a_vanished_row_is_unavailable_with_no_code_and_no_reason(
    two_posts: tuple[Post, Post],
) -> None:
    """No condition's sentence is true of a row that no longer exists, so none is
    reported. The row that still exists is answered exactly as before."""
    spec = _vanishing(UNPUBLISHED, BOOKS_OPEN)
    rows = _rows(spec)

    for render in (render_spec_output, render_for_audience):
        payload = _wire(render(spec, rows, many=True))
        assert payload[0]["affordances"] == {"publish": {"available": False}}
        assert payload[1]["affordances"] == {"publish": _REFUSED}


def test_a_vanished_row_stops_at_its_first_row_condition(two_posts: tuple[Post, Post]) -> None:
    """Sized with an unmet callable declared *after* the row condition: the walk stops
    at the missing row, so the later callable's sentence is not reported."""
    spec = _vanishing(UNPUBLISHED, CLOSED)

    payload = _wire(render_spec_output(spec, _rows(spec), many=True))

    assert payload[0]["affordances"] == {"publish": {"available": False}}


def test_a_vanished_row_still_reports_an_unmet_callable_declared_first(
    two_posts: tuple[Post, Post],
) -> None:
    """A callable condition is not about the row: declared first and unmet, it is the
    genuine first refusal, and its sentence is true whether or not the row exists."""
    spec = _vanishing(CLOSED, UNPUBLISHED)
    closed = {
        "available": False,
        "code": "books_closed",
        "reason": "Publishing is paused until the books close.",
    }

    rows = _rows(spec)
    for render in (render_spec_output, render_for_audience):
        payload = _wire(render(spec, rows, many=True))
        assert payload[0]["affordances"] == {"publish": closed}
        assert payload[1]["affordances"] == {"publish": closed}


def test_a_vanished_rows_answer_validates_against_the_generated_schemas(
    two_posts: tuple[Post, Post],
) -> None:
    spec = _vanishing(UNPUBLISHED, BOOKS_OPEN)
    rows = _rows(spec)
    browser = spec_to_json_schema(spec, phase="output")
    agent = output_to_json_schema(
        _PostOut,
        kind=SelectorKind.LIST,
        projection=AudienceProjection(),
        affordances=spec.affordances,
    )

    served = _wire(render_spec_output(spec, rows, many=True))
    assert served[0]["affordances"] == {"publish": {"available": False}}
    Draft202012Validator(browser).validate(served)
    Draft202012Validator(agent).validate(_wire(render_for_audience(spec, rows, many=True)))


# --- a raw dataclass output ---------------------------------------------------------------


@dataclasses.dataclass
class _PostRow:
    """Declared as the output itself: it renders through a ``DataclassSerializer``."""

    id: int
    title: str


def test_a_raw_dataclass_output_renders_serves_and_describes_the_answers(
    two_posts: tuple[Post, Post],
) -> None:
    """The two features together, at every site that renders them: the output class
    is resolved from the dataclass, the answers are added to what it renders, and the
    payload each audience gets validates against the schema generated for it."""
    draft, out = two_posts
    spec = _listing(output_serializer=_PostRow, affordances={"publish": PUBLISH})
    expected = [
        {"id": draft.pk, "title": "draft", "affordances": {"publish": {"available": True}}},
        {"id": out.pk, "title": "out", "affordances": {"publish": _REFUSED}},
    ]
    rows = _rows(spec)

    browser = _wire(render_spec_output(spec, rows, many=True))
    agent = _wire(render_for_audience(spec, rows, many=True))

    class _View(SelectorListView):
        pass

    _View.spec = spec
    served = json.loads(_View.as_view()(APIRequestFactory().get("/")).render().content)

    assert browser == agent == served == expected
    browser_schema = spec_to_json_schema(spec, phase="output")
    agent_schema = output_to_json_schema(
        _PostRow,
        kind=SelectorKind.LIST,
        projection=audience_projection_for_spec(spec),
        affordances=spec.affordances,
    )
    assert browser_schema is not None
    assert agent_schema is not None
    for schema in (browser_schema, agent_schema):
        assert schema["items"]["properties"]["affordances"]["properties"]["publish"]["properties"][
            "reason"
        ] == {"type": "string"}
        assert schema["items"]["required"] == ["id", "title", "affordances"]
    Draft202012Validator(browser_schema).validate(browser)
    Draft202012Validator(browser_schema).validate(served)
    Draft202012Validator(agent_schema).validate(agent)
