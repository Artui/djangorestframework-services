"""Internal helpers shared across the ``types`` package.

``pk_input_targets`` is the one exception to the package name: the write
path shares it, so the three spellings of a primary key are written down
once rather than agreed between two modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model

from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_orphan import RelationOrphan


def validate_metadata(metadata: Any, *, label: str) -> None:
    """Reject a non-mapping ``metadata`` declaration on a spec.

    Called from ``__post_init__``, not from ``as_view()`` like every other spec
    field: ``metadata`` is read by consumers that may never mount the spec on a
    view, so a view-time check would skip the paths the field exists for.
    Shape-only — the framework never reads the mapping's contents.
    """
    if metadata is None or isinstance(metadata, Mapping):
        return
    raise ImproperlyConfigured(
        f"{label}.metadata must be a mapping (or None); got "
        f"{type(metadata).__name__}. The framework never reads its contents, "
        f"but the shape is fixed so consumers can rely on it."
    )


VALID_RELATION_MODES = tuple(mode.value for mode in RelationMode)


def validate_relation_mode(mode: str, *, label: str) -> None:
    """Reject a ``mode`` that is neither ``"replace"`` nor ``"merge"``.

    Shared by every relation kind that reconciles a collection so the two words
    mean the same thing on all of them.
    """
    if mode not in VALID_RELATION_MODES:
        raise ValueError(f"{label}.mode must be one of {VALID_RELATION_MODES}; got {mode!r}.")


VALID_RELATION_ORPHANS = tuple(orphan.value for orphan in RelationOrphan)


def validate_relation_orphan(orphan: str, *, delete_service: Any, label: str) -> None:
    """Check ``orphan``, and refuse it beside the service that would silence it.

    A ``delete_service`` replaces the unlink-or-delete rule outright, so an
    explicit ``orphan`` beside one would decide nothing and be silently ignored.
    ``AUTO`` is exempt because it states nothing — it is what every spec written
    before the field existed carries.
    """
    if orphan not in VALID_RELATION_ORPHANS:
        raise ValueError(f"{label}.orphan must be one of {VALID_RELATION_ORPHANS}; got {orphan!r}.")
    if delete_service is None or orphan == RelationOrphan.AUTO:
        return
    raise ImproperlyConfigured(
        f"{label}: orphan={RelationOrphan(orphan).value!r} declared alongside delete_service. "
        "The service replaces the unlink-or-delete rule entirely, so the flag would decide "
        "nothing. Dispose of the row in the service, or drop the service and let orphan= say "
        "what happens to it."
    )


def validate_relation_services(
    *,
    label: str,
    services: Mapping[str, Any],
    shaping: Mapping[str, Any],
) -> None:
    """Refuse a relation spec that declares a row service *and* row shaping.

    A ``create_service`` / ``update_service`` stands in for the mutation-helper
    call, so every knob configuring that call is bypassed for the row — silently,
    unless caught here.

    Callers must keep reconciliation fields (``fk`` / ``match_key`` / ``mode`` /
    ``scope`` / orphan handling) out of both mappings, and must not pass
    ``delete_service`` in ``services``: it replaces the unlink-or-delete rule
    rather than the helper call, so it composes with row shaping.
    """
    declared_services: list[str] = sorted(name for name, value in services.items() if value)
    if not declared_services:
        return
    declared_shaping: list[str] = sorted(name for name, value in shaping.items() if value)
    if not declared_shaping:
        return
    raise ImproperlyConfigured(
        f"{label}: {', '.join(declared_services)} declared alongside "
        f"{', '.join(declared_shaping)}. A row service replaces the helper call "
        "that those configure, so they would be silently ignored. Drop them and "
        "let the service shape the row, or drop the service and let the helper "
        "write it. Reconciliation (fk / match_key / mode / scope / orphan "
        "handling) stays with the spec either way."
    )


def pk_input_targets(model: type[Model]) -> frozenset[str]:
    """The names an incoming row can use to mean ``model``'s primary key.

    Three spellings reach the same column and a payload may send any of them:
    ``pk``, the field's ``name``, and its ``attname`` (``id``/``id`` for an
    implicit key, ``author``/``author_id`` where a one-to-one *is* the key).
    """
    return frozenset({"pk", model._meta.pk.name, model._meta.pk.attname})


def validate_pk_field_map(
    *,
    label: str,
    model: type[Model],
    match_key: str,
    field_map: Mapping[str, str] | None,
) -> None:
    """Refuse a ``field_map`` that renames an input key onto the primary key,
    on a spec that also matches *by* the primary key.

    The two readers of a nested row disagree about ``field_map``, and where the
    match key is the primary key that disagreement leaves no working payload.
    Matching reads the row exactly as it arrived (``item[match_key]``), so a
    payload spelling its key ``ident`` never matches; the primary-key guard
    *does* fold ``field_map`` in, sees ``ident``, reads it as a key nothing
    matched, and refuses the row. Every payload using the mapped name is
    rejected, and none can ever match.

    Scoped to a primary-key ``match_key`` because that is the whole of what is
    unreachable. Mapped onto a *natural* match key the same declaration is
    coherent -- the row matches on its natural key and the alias goes on
    guarding creates, exactly as a plainly-spelled ``pk`` would.

    Refused rather than resolved: ``match_key`` names an input key on one side
    and a model field on the other, so "apply ``field_map`` to it too" has no
    single correct answer, and a spec no payload can reach is better caught
    where it is written than on the first request that tries.
    """
    targets: frozenset[str] = pk_input_targets(model)
    if match_key not in targets:
        return
    mapped: list[str] = sorted(src for src, dest in (field_map or {}).items() if dest in targets)
    if not mapped:
        return
    raise ImproperlyConfigured(
        f"{label}: field_map renames {', '.join(repr(name) for name in mapped)} onto the "
        f"primary key of {model.__name__}, which match_key={match_key!r} also matches on. "
        "Matching reads the row as it arrived and does not apply field_map, so a payload "
        "using the mapped name can never match, while the primary-key guard does apply it "
        "and refuses the row. Send the key under a name match_key reads, or match on a "
        "field the mapping does not rename."
    )
