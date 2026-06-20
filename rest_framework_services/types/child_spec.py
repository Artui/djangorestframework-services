"""``ChildSpec`` — declarative reverse-FK child-collection write configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import Model

_VALID_MODES = ("replace", "merge")


@dataclass(frozen=True)
class ChildSpec:
    """How to persist one reverse-FK ("one-to-many") child collection.

    Passed in the ``children={relation_name: ChildSpec(...)}`` map of
    :func:`~rest_framework_services.create_from_input` /
    :func:`~rest_framework_services.update_from_input` (and their async
    siblings), and forwarded by the default
    :func:`~rest_framework_services.create_model` /
    :func:`~rest_framework_services.update_model` /
    :func:`~rest_framework_services.delete_model` services. The incoming child
    rows are read from ``data[relation_name]``; each child is persisted by
    running it back through the same mutation helpers, so scalar / m2m / nested
    semantics compose recursively.

    Fields:

    - **``model``** — the child model class.
    - **``fk``** — the name of the child's forward foreign-key field pointing
      at the parent (e.g. ``"author"`` for ``Book.author``). It is set
      automatically on created children and used to resolve the parent's
      reverse manager.
    - **``match_key``** — the field used to pair an incoming row with an
      existing child (default ``"pk"``). An incoming row whose ``match_key``
      matches an existing child updates it; one with no match (or no key) is
      created. The same name is read off both the incoming mapping
      (``item[match_key]``) and the existing instance
      (``getattr(child, match_key)``), so serializers emitting ``"id"`` should
      set ``match_key="id"``.
    - **``mode``** — ``"replace"`` (the default) matches incoming to existing,
      creates new, updates matched, and removes orphans (existing children
      absent from the incoming set); ``"merge"`` upserts only and never removes.
      An orphan is **unlinked** (its ``fk`` set to ``None``) when the FK is
      nullable, else **deleted** — mirroring ``on_delete=SET_NULL`` vs
      ``CASCADE``.
    - **``field_map``** / **``exclude_fields``** — forwarded to the per-child
      ``create_from_input`` / ``update_from_input`` call, exactly as for the
      parent.
    - **``m2m``** — optional callable ``(child_row) -> mapping`` deriving the
      child's many-to-many assignments from its incoming row (the per-child
      analogue of :func:`~rest_framework_services.create_model`'s ``m2m``).
    - **``children``** — a nested ``{relation_name: ChildSpec}`` map for
      grandchildren; recursion follows the declared tree, so depth is bounded
      by how deeply you nest specs.

    The whole parent + children write runs inside the service's atomic block;
    field-level validation stays in the input serializer / dataclass — the
    helper owns persistence only.
    """

    model: type[Model]
    fk: str
    match_key: str = "pk"
    mode: str = "replace"
    field_map: dict[str, str] | None = None
    exclude_fields: list[str] | None = None
    m2m: Callable[[Any], Mapping[str, Any]] | None = None
    children: Mapping[str, ChildSpec] | None = None

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"ChildSpec.mode must be one of {_VALID_MODES}; got {self.mode!r}.")


__all__ = ["ChildSpec"]
