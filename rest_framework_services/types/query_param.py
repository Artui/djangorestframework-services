"""``QueryParam`` — a read-shaping query param a transport routes to ``request.query_params``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.unset import UNSET


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

    A [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]
    ``filter_set`` does **not** need this — its fields are already generated into the
    schema and flow through as ordinary ``params``.

    Declared here rather than in each adapter for the same reason as
    [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]: it is the same
    declaration whichever transport carries it. Pair it with
    [`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names].

    - ``name`` — the argument / query-string key. Must not collide with a reserved
      transport key; see
      [`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names].
    - ``type`` — the JSON-Schema type advertised to the caller (``"string"`` by default;
      ``"integer"`` / ``"number"`` / ``"boolean"`` / ``"array"`` …).
    - ``description`` — optional help text shown to the caller.
    - ``default`` — value seeded when the caller omits the argument; also surfaced
      as the schema ``default``. Left at ``UNSET`` there is no default, and the
      schema carries no ``default`` key; ``default=None`` is a real declaration
      ("defaults to null") and is surfaced like any other value. Read it with
      ``is not UNSET``, never with a truthiness or ``is not None`` test.

    **An explicit null from the caller is not a supplied value.** Over HTTP a
    query param is always a string, so there is no value a caller can send that
    means null; off-HTTP, ``{"fields": null}`` is the shape a model emits for a
    param it chose not to fill. A transport treats it as an omitted argument —
    the ``default`` still applies — rather than routing ``None`` onto
    ``request.query_params``.

    **No ``required`` flag, deliberately.** A query param is *read-shaping* —
    omitting one is legitimate by construction, and the spec runs correctly
    without it. Requiredness belongs to inputs the spec cannot run without, which
    is [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] and the
    ``InputRequired`` marker.
    """

    name: str
    type: str = "string"
    description: str | None = None
    default: Any = UNSET

    def json_schema(self) -> dict[str, Any]:
        """The JSON-Schema property this param contributes to an input schema.

        ``default`` is emitted whenever one was declared — ``UNSET`` is the
        "no default" sentinel, so an explicit ``default=None`` reaches the
        schema as ``"default": None`` instead of vanishing.
        """
        schema: dict[str, Any] = {"type": self.type}
        if self.description is not None:
            schema["description"] = self.description
        if self.default is not UNSET:
            schema["default"] = self.default
        return schema


__all__ = ["QueryParam"]
