"""The ``UNSET`` sentinel.

Used to distinguish "field omitted from input" from "field explicitly set to
``None``". Critical for partial updates where ``None`` must not stomp on an
existing value.
"""

from __future__ import annotations


class _Unset:
    """Singleton sentinel type. Always falsy; identity-equal to itself only."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> _Unset:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> _Unset:
        return self


UNSET: _Unset = _Unset()
