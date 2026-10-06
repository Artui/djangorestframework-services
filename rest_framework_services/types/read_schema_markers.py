"""``read_schema_markers`` — strip ``Annotated`` and read the schema markers off it."""

from __future__ import annotations

import types
from typing import Annotated, Any, Union, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput

_NONE_TYPE = type(None)
# Both union types. Before 3.14, ``X | None`` is a ``types.UnionType`` only when
# ``X`` is a class or a builtin generic (``int | None``, ``list[...] | None``). Over
# a typing form the ``|`` is that form's own, which returns a ``typing.Union`` as
# ``Optional[X]`` does, so ``Annotated[...] | None`` -- the one ``Optional`` read
# through below -- is a ``typing.Union`` in either spelling. 3.14 makes the two one
# type. Held by test_an_annotated_member_makes_a_typing_union.
_UNION_ORIGINS: tuple[Any, ...] = (Union, types.UnionType)


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

    **Where a marker may sit.** On the annotation itself, or inside exactly one
    level of ``Optional`` the author wrote out: ``Annotated[int, NotClientInput] |
    None`` (or ``Optional[Annotated[...]]``) declares what ``Annotated[int | None,
    NotClientInput]`` does, so both are read, and the first returns its type as
    ``int | None`` so the schema keeps its ``null`` branch. Anything wider raises
    ``ImproperlyConfigured`` rather than guessing what the marker was meant to
    cover: a union with another member beside the marked one, two marked members,
    or a marker nested inside a container (``list[Annotated[int,
    NotClientInput]]``) or under another ``Annotated``. ``InputDescription`` is
    placed by the same rule, here, so the two readers cannot disagree about it.
    Only this package's markers are placed: another library's metadata stays
    legal at any depth.
    """
    underlying, metadata = _marker_layer(annotation)
    # Markers are identity-compared: they are singletons.
    required = any(entry is InputRequired for entry in metadata)
    hidden = any(entry is NotClientInput for entry in metadata)
    if required and hidden:
        raise ImproperlyConfigured(
            f"{annotation!r}: an input cannot be both InputRequired and NotClientInput "
            "— the caller cannot be required to supply a value it is never told about."
        )
    return underlying, required, hidden


def _marker_layer(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """``(type, metadata)``: the one ``Annotated`` layer markers are read from.

    ``read_input_description`` locates the same layer by the same rule, and calls
    this module for the refusals, so the rule is stated twice and enforced once.
    """
    if get_origin(annotation) is Annotated:
        # ``Annotated[T, ...]`` always carries the underlying type first, then >=1
        # metadata entries.
        underlying, *metadata = get_args(annotation)
        _refuse_misplaced_markers(underlying, annotation)
        return underlying, tuple(metadata)
    if get_origin(annotation) in _UNION_ORIGINS:
        members: tuple[Any, ...] = get_args(annotation)
        others = [member for member in members if member is not _NONE_TYPE]
        # A union has at least two distinct members, so exactly one that is not
        # ``None`` means the union is ``X | None``: the one ``Optional`` read
        # through. One arc to coverage, so each operand is named: the count by
        # test_read_schema_markers_refuses[marked-member-beside-a-type] and
        # [marked-member-in-a-wide-optional], which have two, and the
        # ``Annotated`` check by test_a_plain_optional_is_returned_as_written.
        if len(others) == 1 and get_origin(others[0]) is Annotated:
            inner, *metadata = get_args(others[0])
            _refuse_misplaced_markers(inner, annotation)
            # Rebuilt member by member rather than as ``inner | None``, so the
            # author's order (and with it the ``anyOf`` order) is kept, and so a
            # member ``|`` cannot combine (a forward reference) still unions.
            union: Any = Union
            rebuilt = union[tuple(inner if member is others[0] else member for member in members)]
            return rebuilt, tuple(metadata)
    _refuse_misplaced_markers(annotation, annotation)
    return annotation, ()


def _refuse_misplaced_markers(annotation: Any, whole: Any) -> None:
    """Raise if a schema marker sits anywhere in ``annotation``.

    Called on what remains once the layer the markers are read from is taken
    off, so any marker found here is one that would otherwise be ignored.
    """
    if _carries_a_marker(annotation):
        raise ImproperlyConfigured(
            f"{whole!r}: a schema marker (InputRequired, NotClientInput or "
            "InputDescription) is read only on the outermost layer of an input's "
            "annotation, or inside one Optional around it. This one sits deeper, where "
            "it would describe part of the type rather than the input. Put it on the "
            "outermost layer: Annotated[X, Marker], or Annotated[X | None, Marker] for "
            "an input that may be null."
        )


def _carries_a_marker(annotation: Any) -> bool:
    if get_origin(annotation) is Annotated:
        underlying, *metadata = get_args(annotation)
        # Another library's ``Annotated`` is looked through rather than stopped
        # at. Held by test_read_schema_markers_refuses[nested-under-foreign-annotated].
        return any(_is_marker(entry) for entry in metadata) or _carries_a_marker(underlying)
    for arg in get_args(annotation):
        # ``Callable[[A, B], R]`` carries its parameters as a list among the args.
        # Held by test_read_schema_markers_refuses[nested-in-callable-parameters].
        nested = arg if isinstance(arg, list) else [arg]
        if any(_carries_a_marker(item) for item in nested):
            return True
    return False


def _is_marker(entry: Any) -> bool:
    # One boolean, so coverage cannot see a deleted operand; each is held by a
    # case of test_read_schema_markers_refuses: InputRequired by
    # [nested-in-a-mapping-value], NotClientInput by [nested-in-a-container],
    # InputDescription by [nested-description].
    return entry is InputRequired or entry is NotClientInput or isinstance(entry, InputDescription)


__all__ = ["read_schema_markers"]
