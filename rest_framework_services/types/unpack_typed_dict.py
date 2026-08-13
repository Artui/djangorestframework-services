"""``unpack_typed_dict`` — recover the ``TypedDict`` behind an ``Unpack[...]``."""

from __future__ import annotations

from typing import Any, get_args, get_origin

from typing_extensions import Unpack, is_typeddict


def unpack_typed_dict(annotation: Any) -> type | None:
    """Return the ``TypedDict`` class ``T`` when ``annotation`` is ``Unpack[T]``.

    The blessed strict-typing idiom for a service / selector's keyword extras is
    ``**extras: Unpack[SomeExtras]`` (see [`HttpExtras`][rest_framework_services.types.http_extras.HttpExtras]).
    Given the resolved annotation of such a ``**kwargs`` parameter, this returns
    the underlying ``TypedDict`` so its keys can be reflected into a JSON Schema
    (``jsonschema``) and counted as the callable's declared input surface
    (``dispatch``). Returns ``None`` for a bare ``**kwargs``, a non-``Unpack``
    annotation, or an ``Unpack`` of something that isn't a ``TypedDict`` — every
    caller treats ``None`` as "no reflectable extras".

    A parameterised generic alias (``Unpack[HttpExtras[MyUser]]``) is resolved to
    its origin class before the ``TypedDict`` check, since ``is_typeddict`` wants
    the class, not the alias.
    """
    if annotation is None:
        return None
    if get_origin(annotation) is not Unpack:
        return None
    # ``Unpack[X]`` always carries its single argument ``X``.
    candidate: Any = get_args(annotation)[0]
    origin = get_origin(candidate)
    if origin is not None:
        candidate = origin
    return candidate if is_typeddict(candidate) else None


__all__ = ["unpack_typed_dict"]
