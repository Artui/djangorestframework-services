"""``QueryParam`` — a read-shaping query param a transport routes to ``request.query_params``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueryParam:
    """A request-level query param exposed as a caller-supplied argument off-HTTP.

    Generalizes the built-in ``page`` / ``limit`` / ``order`` list-selector
    arguments to any read-shaping param a serializer reads off
    ``request.query_params`` — django-restql field selection (``?query=`` /
    ``?fields=``), or a custom serializer that branches on the query string. The
    transport advertises it, pops it from the arguments, and hands it to
    ``build_offline_context(query_params=…)``; it never reaches the spec as an
    input, so the unknown-argument policy never flags it.

    A :class:`~rest_framework_services.SelectorSpec` ``filter_set`` does **not**
    need this — its fields are already generated into the schema and flow through
    as ordinary ``params``.

    Declared here rather than in each adapter for the same reason as
    :class:`~rest_framework_services.UrlKwarg`: it is the same declaration
    whichever transport carries it. Pair it with
    :func:`~rest_framework_services.validate_channel_names`.

    - ``name`` — the argument / query-string key. Must not collide with a
      reserved transport key; see
      :func:`~rest_framework_services.validate_channel_names`.
    - ``type`` — the JSON-Schema type advertised to the caller (``"string"`` by
      default; ``"integer"`` / ``"number"`` / ``"boolean"`` / ``"array"`` …).
    - ``description`` — optional help text shown to the caller.
    - ``default`` — optional value seeded when the caller omits the argument;
      also surfaced as the schema ``default``.

    **No ``required`` flag, deliberately.** A query param is *read-shaping* —
    omitting one is legitimate by construction, and the spec runs correctly
    without it. Requiredness belongs to inputs the spec cannot run without, which
    is :class:`~rest_framework_services.UrlKwarg` and the
    :data:`~rest_framework_services.InputRequired` marker.
    """

    name: str
    type: str = "string"
    description: str | None = None
    default: Any = None

    def json_schema(self) -> dict[str, Any]:
        """The JSON-Schema property this param contributes to an input schema."""
        schema: dict[str, Any] = {"type": self.type}
        if self.description is not None:
            schema["description"] = self.description
        if self.default is not None:
            schema["default"] = self.default
        return schema


__all__ = ["QueryParam"]
