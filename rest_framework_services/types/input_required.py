"""The ``InputRequired`` schema marker and its type.

Marks a declared input as **required in the generated schema** without making it
required in the *type system*. Use it inside ``Annotated[...]`` on an extras
``TypedDict`` key (or an ordinary parameter) of a service / selector::

    class WidgetExtras(HttpExtras[MyUser], total=False):
        project_pk: Annotated[int, InputRequired]

    @implements(ListSelector[Widget])
    def list_widgets(**extras: Unpack[WidgetExtras]) -> list[Widget]:
        return Widget.objects.filter(project_id=extras["project_pk"])

A required ``TypedDict`` key will not do: under PEP 692 it makes the function
reject callers that omit it, which breaks assignability to
[`ListSelector`][rest_framework_services.selectors.list_selector.ListSelector] /
[`RetrieveSelector`][rest_framework_services.selectors.retrieve_selector.RetrieveSelector] / the service Protocols — the
very reason [`HttpExtras`][rest_framework_services.types.http_extras.HttpExtras] mandates ``total=False``.
``Annotated`` metadata carries no typing weight, so the key stays ``NotRequired``
to the type checker while [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema] lists it in ``required``.

The marker is **advertisement plus enforcement**, never delivery: it does not
change the kwargs pool, the ``view.kwargs``-over-``params`` precedence, or the
``SPREAD_AUTHOR_WINS`` author-beats-client rule. It tells a schema-driven caller
(an MCP client, an LLM tool call) that the input is mandatory, and makes
``dispatch_spec`` raise
[`ServiceValidationError`][rest_framework_services.exceptions.service_validation_error.ServiceValidationError] when it is absent —
instead of the bare ``KeyError`` the callable would otherwise raise from deep
inside dispatch, which no transport maps to a useful error.

Its counterpart is ``NotClientInput``, which hides a
key from the schema entirely. A key marked with both is a contradiction and
raises at schema-generation time.

``InputRequired`` is the singleton you place in the annotation;
``InputRequiredType`` is its type, exported only so it can be spelled in
annotations.
"""

from __future__ import annotations


class InputRequiredType:
    """Singleton marker type. Identity-equal to itself only.

    Don't instantiate this directly — use the module-level ``InputRequired``
    singleton. ``InputRequiredType()`` returns that same instance.
    """

    _instance: InputRequiredType | None = None

    def __new__(cls) -> InputRequiredType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "InputRequired"


InputRequired: InputRequiredType = InputRequiredType()

__all__ = ["InputRequired", "InputRequiredType"]
