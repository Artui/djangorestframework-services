"""``delete_collection`` — default bulk-delete service over a collection target."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services.types.change_result import ModelT


def delete_collection(
    model: type[ModelT],
    *,
    soft_delete: Callable[[Any], None] | None = None,
) -> Callable[..., None]:
    """Return a service that deletes the resolved ``collection`` (bulk).

    Pairs with ``ServiceSpec.collection_selector_spec``, which seeds the
    target set into the pool as ``collection``. Equivalent to::

        def delete_books(*, collection, **_: Any) -> None:
            collection.delete()

    The default calls ``collection.delete()`` — a single queryset bulk delete.
    An **empty** collection is a no-op (deletes nothing), so the action is
    idempotent. ``model`` is accepted for symmetry / type binding (like
    [`delete_model`][rest_framework_services.services.delete_model.delete_model]); the set itself comes from the spec.

    ``soft_delete`` is an optional hook called with the ``collection`` *instead
    of* ``delete()`` — e.g. ``lambda qs: qs.update(is_archived=True)``.
    """

    def _service(*, collection: Any, **kwargs: Any) -> None:
        if soft_delete is not None:
            soft_delete(collection)
        else:
            collection.delete()
        return None

    return _service


__all__ = ["delete_collection"]
