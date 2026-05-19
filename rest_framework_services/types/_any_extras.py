"""Private ``TypedDict`` sentinel used as the default ``ExtraT`` of every
unified service / selector Protocol.

A ``TypedDict`` with no required keys plus :pep:`728`'s ``extra_items=Any``
accepts any keyword argument of type :class:`~typing.Any` when unpacked into a
``**extras`` parameter. Setting it as the default of each Protocol's
``ExtraT`` TypeVar means parameterising ``CreateService[InputT, ResultT]``
(two arguments) reproduces the old "lenient" shape — the kwargs pool delivered
by the framework flows in untyped, exactly like the previous
``**kwargs: Any`` did.

Subclassing :class:`~rest_framework_services.types.http_extras.HttpExtras`
(or any other ``TypedDict``) and supplying it as the third type argument
(``CreateService[InputT, ResultT, MyExtras]``) opts into the strict shape:
type checkers then enforce that the service declares exactly those keys.

Not re-exported from ``rest_framework_services.types`` — this is an
implementation detail of the Protocol defaults, not part of the public API.
The module name is prefixed with ``_`` for the same reason.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class _AnyExtras(TypedDict, extra_items=Any):
    """Default-shape extras: any keyword key, value typed as ``Any``."""
