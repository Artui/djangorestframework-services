"""The ``UNSET`` sentinel and its type.

Used to distinguish "field omitted from input" from "field explicitly set to
``None``". Critical for partial updates where ``None`` must not stomp on an
existing value.

``UNSET`` is the singleton value you compare against (``value is UNSET``).
``UnsetType`` is its type, exported so callers can spell it in annotations —
e.g. ``bio: str | None | UnsetType``.
"""

from __future__ import annotations


class UnsetType:
    """Singleton sentinel type. Always falsy; identity-equal to itself only.

    Don't instantiate this directly — use the module-level ``UNSET`` singleton.
    ``UnsetType()`` returns that same instance, but the sentinel is the value
    you compare against and ``UnsetType`` is only useful as a type annotation.
    """

    _instance: UnsetType | None = None

    def __new__(cls) -> UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> UnsetType:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> UnsetType:
        return self


UNSET: UnsetType = UnsetType()
