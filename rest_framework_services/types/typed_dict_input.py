"""``typed_dict_input`` — resolved field types + required keys of a ``TypedDict``."""

from __future__ import annotations

from typing import Any, get_args, get_origin

from typing_extensions import NotRequired, Required, get_type_hints


def typed_dict_input(td: type) -> tuple[dict[str, Any], frozenset[str]]:
    """Return ``(field_types, required_keys)`` for a ``TypedDict``.

    ``field_types`` maps each key to its resolved annotation with any
    ``Required[...]`` / ``NotRequired[...]`` wrapper stripped (so callers get the
    bare type to map to JSON Schema). ``required_keys`` is the subset that a
    conforming value must provide.

    **Why not just read ``td.__required_keys__``.** Under ``from __future__ import
    annotations`` (PEP 563 — mandated in this package and common in consumers),
    the annotations are strings at class-creation time, so ``TypedDict`` cannot
    see through ``NotRequired[...]`` and misclassifies such a key as *required*
    (and drops it from ``__optional_keys__``). We therefore start from
    ``__required_keys__`` — which *is* correct for bare keys, honouring ``total``
    and inheritance the way the type system defines it — and correct only the
    wrapper misclassification by re-reading the resolved hints: an explicit
    ``NotRequired`` demotes a key to optional, an explicit ``Required`` promotes
    it. Keys are intersected with the resolved hint set so a stale
    ``__required_keys__`` entry can't leak a phantom key.
    """
    hints = get_type_hints(td, include_extras=True)
    required: set[str] = set(getattr(td, "__required_keys__", frozenset()))
    field_types: dict[str, Any] = {}
    for name, hint in hints.items():
        origin = get_origin(hint)
        if origin is NotRequired:
            required.discard(name)
            hint = get_args(hint)[0]
        elif origin is Required:
            required.add(name)
            hint = get_args(hint)[0]
        field_types[name] = hint
    return field_types, frozenset(required & set(field_types))


__all__ = ["typed_dict_input"]
