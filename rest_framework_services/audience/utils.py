"""Internal helpers for the ``affordances`` projection, shared by every render path.

The payload side and the schema side of one declaration live together here for
the reason ``project_payload`` and ``annotate_output_schema`` sit side by side:
they agree only while they are read together. Nothing here touches a serializer
-- a payload arrives already rendered, and a row is read for the annotations the
list query put on it -- so the projection survives whatever renders the fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.utils import affordance_alias

AFFORDANCES_KEY: Final = "affordances"
"""The key each rendered object carries its answers under -- the field's own name."""

_REASON: Final = "reason"

_MISSING: Final = object()


def rendered_affordances(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
) -> Mapping[str, ServiceSpec[Any, Any, Any]] | None:
    """The ``affordances`` mapping on whichever selector spec ``spec`` renders through.

    A ``SelectorSpec`` carries its own. A ``ServiceSpec`` renders through its
    ``output_selector_spec`` -- and its *own* ``affordances`` are the conditions
    it is checked against, a different declaration that is never rendered, which
    is why this dispatches on the class rather than reading the attribute.
    """
    if isinstance(spec, SelectorSpec):
        return spec.affordances
    nested = spec.output_selector_spec
    return nested.affordances if nested is not None else None


def with_affordances(
    payload: Any, value: Any, affordances: Mapping[str, ServiceSpec[Any, Any, Any]], *, many: bool
) -> Any:
    """``payload`` with each rendered object carrying its row's answers.

    ``value`` is what was rendered: the row, or for ``many`` the materialised rows
    in the order they were rendered, so the two can be walked together. Callers
    skip a ``None`` value, which is not a row and has nothing to report.

    Raises:
        ImproperlyConfigured: A rendered item is not an object to add a key to; it
            already has an ``affordances`` key, which the answers would overwrite;
            or its row carries no answer, because the selector that produced it
            was not the one declaring them.
    """
    if not many:
        return _with_answers(payload, value, affordances)
    return [_with_answers(item, row, affordances) for item, row in zip(payload, value, strict=True)]


def without_affordance_reasons(payload: Any, *, many: bool) -> Any:
    """``payload`` with every answer's ``reason`` removed, for an agent audience.

    The code names the rule and is safe for any reader; the reason is an
    operator's sentence and may describe internal state, and a model reads out
    what it is handed. The mirror of ``affordance_schema(include_reason=False)``.
    """
    if not many:
        return _without_reasons(payload)
    return [_without_reasons(item) for item in payload]


def affordance_schema(
    affordances: Mapping[str, ServiceSpec[Any, Any, Any]], *, include_reason: bool
) -> dict[str, Any]:
    """The JSON Schema for the ``affordances`` object ``with_affordances`` adds.

    One property per declared name, each an object whose ``available`` is always
    present and whose ``code`` -- enumerated from the declaration, so a client can
    switch on it exhaustively -- and ``reason`` appear only when it is ``false``.
    ``include_reason=False`` is the agent audience's mirror of
    ``without_affordance_reasons``.
    """
    properties: dict[str, Any] = {}
    for name, service_spec in affordances.items():
        answer: dict[str, Any] = {"available": {"type": "boolean"}}
        codes = [affordance.code for affordance in service_spec.affordances or ()]
        if codes:
            answer["code"] = {"type": "string", "enum": codes}
            if include_reason:
                answer[_REASON] = {"type": "string"}
        properties[name] = {"type": "object", "properties": answer, "required": ["available"]}
    return {"type": "object", "properties": properties, "required": list(affordances)}


def _with_answers(
    item: Any, row: Any, affordances: Mapping[str, ServiceSpec[Any, Any, Any]]
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ImproperlyConfigured(
            "affordances are projected into each rendered object, and this spec rendered "
            f"{type(item).__name__}. Declare an output_serializer."
        )
    if AFFORDANCES_KEY in item:
        raise ImproperlyConfigured(
            f"The rendered object already has an {AFFORDANCES_KEY!r} key, which the declared "
            "affordances would overwrite. Rename the serializer field."
        )
    return {**item, AFFORDANCES_KEY: _answers(row, affordances)}


def _answers(
    row: Any, affordances: Mapping[str, ServiceSpec[Any, Any, Any]]
) -> dict[str, dict[str, Any]]:
    """Each declared name's answer for ``row``: the first unmet condition, in order."""
    answers: dict[str, dict[str, Any]] = {}
    for name, service_spec in affordances.items():
        answer: dict[str, Any] = {"available": True}
        for affordance in service_spec.affordances or ():
            if not _flag(row, affordance_alias(name, affordance.code)):
                answer = {"available": False, "code": affordance.code, _REASON: affordance.reason}
                break
        answers[name] = answer
    return answers


def _flag(row: Any, alias: str) -> Any:
    """The annotation's value on a model row, or on a ``.values()`` row."""
    flag: Any = (
        row.get(alias, _MISSING) if isinstance(row, Mapping) else getattr(row, alias, _MISSING)
    )
    if flag is _MISSING:
        raise ImproperlyConfigured(
            f"The rendered row carries no {alias!r} annotation. Affordance answers are "
            "computed by the selector spec that declares them; a value that did not come "
            "through that selector -- or a spec whose selector is not set -- has none."
        )
    return flag


def _without_reasons(item: Mapping[str, Any]) -> dict[str, Any]:
    """One rendered object without its answers' reasons.

    Only ever handed an object ``with_affordances`` produced, which is what lets
    this read the key rather than check for it.
    """
    return {
        **item,
        AFFORDANCES_KEY: {
            name: {key: value for key, value in answer.items() if key != _REASON}
            for name, answer in item[AFFORDANCES_KEY].items()
        },
    }
