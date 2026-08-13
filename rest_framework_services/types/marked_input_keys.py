"""``marked_input_keys`` — the schema-marked keys of a service / selector callable."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict


def marked_input_keys(fn: Callable[..., Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(required, hidden)`` — the keys ``fn`` marks with the schema markers.

    Covers the same surface ``callable_input_schema`` reflects: ordinary
    keyword-acceptable parameters, and the keys of a
    ``**kwargs: Unpack[SomeExtras]`` ``TypedDict``. ``required`` is the set marked
    ``InputRequired``, ``hidden`` the set marked
    ``NotClientInput``. A callable with no markers
    returns two empty sets — nothing to enforce, nothing to hide — which is the
    overwhelmingly common case.

    Unresolvable annotations yield empty sets rather than raising: a marker that
    cannot be read is a marker that cannot be honoured, and schema generation is
    best-effort throughout this package.
    """
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 — never fatal, see the docstring
        return frozenset(), frozenset()
    required: set[str] = set()
    hidden: set[str] = set()

    def _record(name: str, hint: Any) -> None:
        _underlying, is_required, is_hidden = read_schema_markers(hint)
        if is_required:
            required.add(name)
        if is_hidden:
            hidden.add(name)

    for name, parameter in inspect.signature(fn).parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            typed_dict = unpack_typed_dict(hints.get(name))
            if typed_dict is not None:
                for key, hint in typed_dict_input(typed_dict)[0].items():
                    _record(key, hint)
        elif parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            if name in hints:
                _record(name, hints[name])
    return frozenset(required), frozenset(hidden)


__all__ = ["marked_input_keys"]
