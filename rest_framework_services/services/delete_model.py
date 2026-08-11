"""``delete_model`` — default delete-action service factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.mutations.utils import delete_relations, merge_relations
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


def delete_model(
    model: type[ModelT],
    *,
    soft_delete: Callable[[ModelT], None] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    relations: Mapping[str, RelationSpec] | None = None,
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

    ``relations`` (and its reverse-FK alias ``children``) declares what to
    remove **before** the parent goes, deepest first. Use it to cascade
    explicitly when the database can't: a ``PROTECT`` relation, or a
    ``soft_delete`` Django never cascades through because no row is deleted.

    The same map the write path takes, and the same one rule applies to every
    kind: **the cascade removes the rows the parent owns and leaves the rows it
    merely points at alone.** A reverse-FK collection, a reverse one-to-one and
    a generic relation are the parent's rows and go, nullable links unlinked
    and the rest deleted; a many-to-many loses only its membership, since the
    targets are shared; a forward relation is left untouched, because the
    column holding it goes with the parent. The specs' write-only fields
    (``match_key`` / ``mode`` / ``field_map`` / ``m2m``) are ignored here.

    The rest of the framework's kwargs pool is handed on as
    :func:`~rest_framework_services.mutations.utils.delete_relations`'s
    ``context=``, so a per-row service declared on a spec can see who is
    calling (see :func:`create_model`).
    """
    declared = merge_relations(children, relations)

    def _service(*, instance: ModelT, **kwargs: Any) -> None:
        if declared:
            delete_relations(instance, declared, context=kwargs)
        if soft_delete is not None:
            soft_delete(instance)
        else:
            instance.delete()
        return None

    return _service
