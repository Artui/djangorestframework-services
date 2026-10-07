"""``marked_input_keys`` — the schema-marked keys of a service / selector callable."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.utils import callable_type_hints


def marked_input_keys(fn: Callable[..., Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(required, hidden)`` — the keys ``fn`` marks with the schema markers.

    Covers the same surface ``callable_input_schema`` reflects: ordinary
    keyword-acceptable parameters, and the keys of a
    ``**kwargs: Unpack[SomeExtras]`` ``TypedDict``. ``required`` is the set marked
    ``InputRequired``, ``hidden`` the set marked
    ``NotClientInput``. A callable with no markers
    returns two empty sets — nothing to enforce, nothing to hide — which is the
    overwhelmingly common case.

    An annotation that does not resolve costs only its own name: the markers on
    every other parameter and key are read as written, and its own are read as
    far as ``callable_type_hints`` can take it, which fails closed. A
    ``**kwargs: Unpack[SomeExtras]`` whose ``TypedDict`` does not resolve marks no
    key, since nothing says which keys it has.

    Raises ``ImproperlyConfigured`` for a marker ``read_schema_markers`` cannot
    place, or for a key marked both ``InputRequired`` and ``NotClientInput``.
    """
    hints = callable_type_hints(fn)
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
