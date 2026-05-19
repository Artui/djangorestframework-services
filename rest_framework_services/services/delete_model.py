"""``delete_model`` — default delete-action service factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services.types.change_result import ModelT


def delete_model(
    model: type[ModelT],
    *,
    soft_delete: Callable[[ModelT], None] | None = None,
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
    """

    def _service(*, instance: ModelT, **kwargs: Any) -> None:
        if soft_delete is not None:
            soft_delete(instance)
        else:
            instance.delete()  # type: ignore[attr-defined]
        return None

    return _service
