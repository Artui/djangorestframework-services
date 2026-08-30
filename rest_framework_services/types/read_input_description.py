"""``read_input_description`` — read the description marker off an annotation."""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.not_client_input import NotClientInput


def read_input_description(annotation: Any) -> str | None:
    """Return the ``InputDescription`` text on a possibly-``Annotated`` annotation.

    ``None`` when there is none, which is the overwhelmingly common case: a plain
    annotation, or an ``Annotated`` carrying only the other markers or another
    library's metadata.

    Separate from ``read_schema_markers`` rather than a fourth element of its
    tuple, because that function's shape is public and every caller unpacks it
    positionally; widening it would break each one for no gain. The two are read
    side by side wherever both matter.

    Two refusals, both because the alternative decides nothing silently:

    - **Two descriptions on one input.** Picking the first or the last would be
      an arbitrary rule a reader has to know, and the schema can publish only
      one.
    - **A description beside ``NotClientInput``.** That marker drops the key
      from the schema entirely, so the text has nowhere to land. Unlike the
      ``InputRequired`` / ``NotClientInput`` pair this is not a contradiction —
      it is merely useless — but a declaration that is silently ignored is
      exactly what this package refuses elsewhere, and the fix (delete one of
      the two markers) is obvious once it is named.
    """
    if get_origin(annotation) is not Annotated:
        return None
    # ``Annotated[T, ...]`` always carries the underlying type first, then >=1
    # metadata entries.
    _underlying, *metadata = get_args(annotation)
    declared: list[InputDescription] = [
        entry for entry in metadata if isinstance(entry, InputDescription)
    ]
    if not declared:
        return None
    if len(declared) > 1:
        raise ImproperlyConfigured(
            f"{annotation!r}: {len(declared)} InputDescription markers on one input. "
            "A schema publishes one description, so keep the sentence that says what "
            "the input is for and drop the rest."
        )
    if any(entry is NotClientInput for entry in metadata):
        raise ImproperlyConfigured(
            f"{annotation!r}: an input cannot be both described and NotClientInput — the "
            "key is dropped from the schema, so the description has no caller to reach. "
            "Drop the description, or drop NotClientInput if the caller should see the key."
        )
    return declared[0].text


__all__ = ["read_input_description"]
