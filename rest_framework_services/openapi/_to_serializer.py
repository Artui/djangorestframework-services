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
from rest_framework_dataclasses.serializers import DataclassSerializer


def to_serializer_class(value: type | None) -> type[Serializer] | None:
    """Coerce a spec serializer field to a ``Serializer`` subclass.

    Returns ``None`` when ``value`` is ``None`` or an unrecognised shape.
    Bare dataclass types are wrapped in a one-off ``DataclassSerializer``
    subclass so schema generators see typed properties rather than ``object``.
    """
    if value is None:
        return None
    if isinstance(value, type) and issubclass(value, Serializer):
        return value
    if is_dataclass(value):
        return type(
            f"_AutoDataclassSerializer_{value.__name__}",
            (DataclassSerializer,),
            {"Meta": type("Meta", (), {"dataclass": value})},
        )
    return None
