"""``delete_model`` — default delete-action service factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.mutations.utils import delete_children
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec


def delete_model(
    model: type[ModelT],
    *,
    soft_delete: Callable[[ModelT], None] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
) -> Callable[..., None]:
    """Return a service callable that deletes the resolved instance.

    Equivalent to::

        def delete_author(*, instance: Author, **_: Any) -> None:
            instance.delete()

    ``model`` is accepted for symmetry / type binding; the instance comes
    from the view's ``get_object()``.

    ``soft_delete`` is an optional hook called *instead of*
    ``instance.delete()`` — covers the common archive case::

        def _archive(instance: Author) -> None:
            instance.is_archived = True
            instance.save(update_fields=["is_archived"])

        delete_model(Author, soft_delete=_archive)

    ``children`` declares reverse-FK collections to remove **before** the
    parent goes (grandchildren first), each nullable-FK child unlinked and the
    rest deleted — mirroring ``on_delete=SET_NULL`` / ``CASCADE``. Use it to
    cascade explicitly when the FK can't (a ``PROTECT`` relation, or a
    ``soft_delete`` that Django won't cascade through). The ``ChildSpec``'s
    write-only fields (``match_key`` / ``mode`` / ``field_map`` / ``m2m``) are
    ignored here; only ``model`` / ``fk`` / ``children`` apply.
    """

    def _service(*, instance: ModelT, **kwargs: Any) -> None:
        if children is not None:
            delete_children(instance, children)
        if soft_delete is not None:
            soft_delete(instance)
        else:
            instance.delete()  # type: ignore[attr-defined]
        return None

    return _service
