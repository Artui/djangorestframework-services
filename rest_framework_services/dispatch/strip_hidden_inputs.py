"""``strip_hidden_inputs`` — drop a caller's values for ``NotClientInput`` keys and ``view``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.dispatch.utils import hidden_input_keys


def strip_hidden_inputs(params: Mapping[str, Any], fn: Callable[..., Any]) -> Mapping[str, Any]:
    """Drop the keys ``fn`` marks ``NotClientInput``, and ``view``, from a caller's mapping.

    The marker says the caller never supplies the key, so dispatch removes the
    caller's value before it can be spread into ``fn``'s pool, whatever the
    ``UnknownArguments`` policy and whatever the binding's precedence. Apply it to
    the caller's mapping only, never to the pool: a ``spec.kwargs`` provider, a
    route capture, a registered pool seed and the parameter's default are all
    still how such a key is filled.

    ``view`` goes the same way for every ``fn``, marked or not (see
    ``hidden_input_keys``): no pool carries it and the input schema hides it, so a
    caller's value would otherwise be the only one a selector declaring it ever
    received, and on ``SPREAD_CALLER_WINS`` it would outrank a provider's. Held by
    ``test_a_callers_view_never_reaches_a_selector`` and the tests beside it in
    ``tests/dispatch/test_dispatch_view_input.py``.

    Both cores call it at every site where caller input is spread into a pool --
    a selector's own spread, a single-item service's spread and the extras it is
    handed beyond its input serializer (the ``PASSTHROUGH`` extras, and with no
    serializer under a ``SPREAD_*`` binding the caller's values for its own
    parameters, all of them when it declares a bare ``**kwargs``), and the two
    target lookups, whose pools are built by hand. A ``many=True`` service spreads nothing: its items reach it inside the
    one ``data`` list and never as keyword arguments.

    Returns ``params`` itself when it carries none of those keys, which is nearly
    always, so the common case neither copies nor changes the mapping's type.
    """
    hidden = hidden_input_keys(fn)
    if hidden.isdisjoint(params):
        return params
    return {key: value for key, value in params.items() if key not in hidden}


__all__ = ["strip_hidden_inputs"]
