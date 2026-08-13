"""``read_schema_markers`` — strip ``Annotated`` and read the schema markers off it."""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput


def read_schema_markers(annotation: Any) -> tuple[Any, bool, bool]:
    """Return ``(type, required, hidden)`` for a possibly-``Annotated`` annotation.

    ``type`` is the annotation with any ``Annotated[...]`` wrapper removed, so
    callers map the *underlying* type to JSON Schema. ``required`` is ``True``
    when ``InputRequired`` is among the metadata;
    ``hidden`` is ``True`` for ``NotClientInput``.
    A plain annotation returns ``(annotation, False, False)``.

    **Stripping matters even with no markers.** ``Annotated[int, "help text"]``
    is a legal annotation a consumer may already be using for an unrelated reason;
    without stripping, ``_python_type_to_schema`` sees an alias it doesn't
    recognise and yields ``{}`` — an *untyped* property — where bare ``int``
    yields ``{"type": "integer"}``. So the strip is a fix in its own right, not
    just plumbing for the markers.

    Foreign metadata (a ``Field(...)``, a docstring, another library's marker) is
    ignored rather than rejected — ``Annotated`` is a shared channel and this is
    not the only consumer of it.

    Marking a key both required and hidden is a contradiction — "the caller must
    supply this" and "the caller must never learn it exists" cannot both hold —
    so it raises ``ImproperlyConfigured`` at schema-generation time rather than
    silently resolving one way.
    """
    if get_origin(annotation) is not Annotated:
        return annotation, False, False
    # ``Annotated[T, ...]`` always carries the underlying type first, then >=1
    # metadata entries. Markers are identity-compared: they are singletons.
    underlying, *metadata = get_args(annotation)
    required = any(entry is InputRequired for entry in metadata)
    hidden = any(entry is NotClientInput for entry in metadata)
    if required and hidden:
        raise ImproperlyConfigured(
            f"{annotation!r}: an input cannot be both InputRequired and NotClientInput "
            "— the caller cannot be required to supply a value it is never told about."
        )
    return underlying, required, hidden


__all__ = ["read_schema_markers"]
