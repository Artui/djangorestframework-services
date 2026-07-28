"""``UrlKwarg`` — a URL-derived value a transport advertises and routes to ``view.kwargs``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UrlKwarg:
    """A URL route capture exposed as a caller-supplied argument off-HTTP.

    Over HTTP a nested route's captures (the ``project_pk`` of
    ``/projects/{project_pk}/widgets/``) reach a spec through ``view.kwargs`` —
    directly, or through a ``spec.kwargs`` provider that scopes by them.
    Off-HTTP there is no route, so the caller supplies the value as an ordinary
    argument: the transport advertises it in the tool / operation schema, pops it
    out of the arguments, and hands it to ``build_offline_context(kwargs=…)``,
    from where :func:`~rest_framework_services.dispatch_spec` spreads it into the
    selector / target pools — authoritative over the spec params, below a
    ``spec.kwargs`` provider. It never reaches the spec as an ordinary input, so
    the unknown-argument policy never flags it.

    **This type is declared here, not in each adapter, on purpose.** It is the
    same declaration whichever transport carries it, and two independent copies
    had already drifted into validating the same declaration against different
    reserved-name sets. Adapters import it and pair it with
    :func:`~rest_framework_services.validate_channel_names`.

    Reach for a ``UrlKwarg`` when the value is a URL-derived input a spec depends
    on that is **not** already an ordinary argument — most commonly a scoping
    ``spec.kwargs`` provider reading ``view.kwargs`` (off-HTTP that mapping is
    otherwise empty, so the provider mis-scopes for every caller), or a
    closed-surface spec whose route capture must be caller-suppliable.

    A selector that reads the value from its own ``**extras: Unpack[TypedDict]``
    needs **no** ``UrlKwarg``: drf-services reflects the key into the schema and
    ``params`` delivers it. A key can be *both* reflected and registered — the
    explicit ``UrlKwarg`` wins the adapter's schema merge, registration pops the
    argument into ``kwargs=``, and the authoritative spread still delivers it to
    the selector pool, so both readers see it.

    - ``name`` — the argument / view-kwarg key. Must not collide with a reserved
      transport key; see :func:`~rest_framework_services.validate_channel_names`.
    - ``type`` — the JSON-Schema type advertised to the caller (``"string"`` by
      default; ``"integer"`` / ``"number"`` / ``"boolean"`` …).
    - ``description`` — optional help text shown to the caller.
    - ``default`` — optional value seeded when the caller omits the argument;
      also surfaced as the schema ``default``.
    - ``required`` — advertise the key in the schema's ``required`` list. Use it
      for a route capture the spec genuinely cannot run without, so a caller is
      told up front instead of failing mid-dispatch. Setting both ``required``
      and a ``default`` is contradictory and raises in
      :func:`~rest_framework_services.validate_channel_names`.

    ``required`` here is the registered-declaration counterpart of the
    :data:`~rest_framework_services.InputRequired` marker, which does the same
    job for a key the callable's own ``TypedDict`` declares. Both end up in the
    schema's ``required``; they differ only in where the key is declared.
    """

    name: str
    type: str = "string"
    description: str | None = None
    default: Any = None
    required: bool = False

    def json_schema(self) -> dict[str, Any]:
        """The JSON-Schema property this kwarg contributes to an input schema."""
        schema: dict[str, Any] = {"type": self.type}
        if self.description is not None:
            schema["description"] = self.description
        if self.default is not None:
            schema["default"] = self.default
        return schema


__all__ = ["UrlKwarg"]
