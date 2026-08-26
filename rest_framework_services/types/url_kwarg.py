"""``UrlKwarg`` — a URL-derived value a transport advertises and routes to ``view.kwargs``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.unset import UNSET


@dataclass(frozen=True)
class UrlKwarg:
    """A URL route capture exposed as a caller-supplied argument off-HTTP.

    Over HTTP a nested route's captures (the ``project_pk`` of
    ``/projects/{project_pk}/widgets/``) reach a spec through ``view.kwargs`` —
    directly, or through a ``spec.kwargs`` provider that scopes by them. Off-HTTP there
    is no route, so the caller supplies the value as an ordinary argument: the transport
    advertises it in the tool / operation schema, pops it out of the arguments, and
    hands it to ``build_offline_context(kwargs=…)``, from where
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]
    spreads it into the selector / target pools — authoritative over the spec params,
    below a ``spec.kwargs`` provider. It never reaches the spec as an ordinary input, so
    the unknown-argument policy never flags it. Adapters import this declaration and
    pair it with
    [`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names].

    Reach for one when the value is a URL-derived input a spec depends on that is
    **not** already an ordinary argument — most commonly a scoping ``spec.kwargs``
    provider reading ``view.kwargs`` (off-HTTP that mapping is otherwise empty, so
    the provider mis-scopes for every caller), or a closed-surface spec whose route
    capture must be caller-suppliable. A selector reading the value from its own
    ``**extras: Unpack[TypedDict]`` needs **no** ``UrlKwarg``: drf-services reflects
    the key into the schema and ``params`` delivers it. A key can be *both*
    reflected and registered — the explicit ``UrlKwarg`` wins the adapter's schema
    merge, registration pops the argument into ``kwargs=``, and the authoritative
    spread still delivers it to the selector pool, so both readers see it.

    **An explicit null from the caller is not a supplied value.** A route capture
    is a URL segment, so over HTTP there is no value that means null; off-HTTP,
    ``{"project_pk": null}`` is what a model emits for a capture it could not
    resolve. A transport treats that as an omitted argument — ``required`` still
    refuses it and ``default`` still applies — rather than spreading ``None`` onto
    ``view.kwargs``, where a scoping provider would turn it into a silent
    ``IS NULL`` lookup that returns rows and looks successful.

    Attributes:
        name: The argument / view-kwarg key. Must not collide with a reserved
            transport key; see
            [`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names].
        type: The JSON-Schema type advertised to the caller — ``"string"`` by
            default, or ``"integer"`` / ``"number"`` / ``"boolean"`` …
        description: Optional help text shown to the caller.
        default: Value seeded when the caller omits the argument; also surfaced as
            the schema ``default``. Left at ``UNSET`` there is no default and the
            schema carries no ``default`` key; ``default=None`` is a real
            declaration ("defaults to null") and is surfaced like any other
            value. Read it with ``is not UNSET``, never with a truthiness or
            ``is not None`` test.
        required: Advertise the key in the schema's ``required`` list. Use it for
            a route capture the spec genuinely cannot run without, so a caller is
            told up front instead of failing mid-dispatch. Setting both
            ``required`` and a ``default`` is contradictory and raises in
            [`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names]. It is the
            registered-declaration counterpart of
            ``InputRequired``, which does the same job
            for a key the callable's own ``TypedDict`` declares; both end up in the
            schema's ``required``.
    """

    name: str
    type: str = "string"
    description: str | None = None
    default: Any = UNSET
    required: bool = False

    def json_schema(self) -> dict[str, Any]:
        """The JSON-Schema property this kwarg contributes to an input schema.

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


__all__ = ["UrlKwarg"]
