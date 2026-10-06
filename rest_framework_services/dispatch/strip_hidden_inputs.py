"""``strip_hidden_inputs`` — drop a caller's values for ``NotClientInput`` keys."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.types.marked_input_keys import marked_input_keys


def strip_hidden_inputs(params: Mapping[str, Any], fn: Callable[..., Any]) -> Mapping[str, Any]:
    """Drop the keys ``fn`` marks ``NotClientInput`` from a caller-supplied mapping.

    The marker says the caller never supplies the key, so dispatch removes the
    caller's value before it can be spread into ``fn``'s pool, whatever the
    ``UnknownArguments`` policy and whatever the binding's precedence. Apply it to
    the caller's mapping only, never to the pool: a ``spec.kwargs`` provider, a
    route capture, a registered pool seed and the parameter's default are all
    still how such a key is filled.

    Both cores call it at every site where caller input is spread into a pool --
    a selector's own spread, a single-item service's spread and the extras it is
    handed beyond its input serializer (the ``PASSTHROUGH`` extras, and with no
    serializer under a ``SPREAD_*`` binding the caller's values for its own
    parameters, all of them when it declares a bare ``**kwargs``), and the two
    target lookups, whose pools are built by hand. A ``many=True`` service spreads nothing: its items reach it inside the
    one ``data`` list and never as keyword arguments.

    Returns ``params`` itself when ``fn`` marks nothing, which is nearly always, so
    the common case neither copies nor changes the mapping's type.
    """
    hidden = marked_input_keys(fn)[1]
    if not hidden:
        return params
    return {key: value for key, value in params.items() if key not in hidden}


__all__ = ["strip_hidden_inputs"]
