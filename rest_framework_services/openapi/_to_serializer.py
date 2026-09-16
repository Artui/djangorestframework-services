"""Convert ``ServiceSpec`` serializer fields into ``Serializer`` classes.

The runtime accepts three shapes for ``input_serializer``:

- ``None`` — no input.
- a ``Serializer`` subclass.
- a bare ``@dataclass`` type, auto-wrapped in a ``DataclassSerializer``.

Schema generators only know how to read ``Serializer`` subclasses, so this helper
mirrors
[`validate_input`][rest_framework_services.views.mutation.utils.validate_input]'s
branching to produce a class the generator can introspect — keeping the schema and the
runtime in lockstep."""

from __future__ import annotations

from dataclasses import is_dataclass

from rest_framework.serializers import Serializer

from rest_framework_services.views.utils import dataclass_serializer_class


def to_serializer_class(value: type | None) -> type[Serializer] | None:
    """Coerce a spec serializer field to a ``Serializer`` subclass.

    Returns ``None`` when ``value`` is ``None`` or an unrecognised shape.
    Bare dataclass types are wrapped in a ``DataclassSerializer`` subclass so
    schema generators see typed properties rather than ``object`` — the same
    class, per dataclass, that the render path serializes through.
    """
    if value is None:
        return None
    if isinstance(value, type) and issubclass(value, Serializer):
        return value
    if is_dataclass(value):
        return dataclass_serializer_class(value)
    return None
