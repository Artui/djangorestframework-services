"""Internal helpers shared across the ``types`` package."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.relation_mode import RelationMode


def validate_metadata(metadata: Any, *, label: str) -> None:
    """Reject a non-mapping ``metadata`` declaration on a spec.

    Called from ``__post_init__``, unlike every other spec field — those are
    checked at ``as_view()`` time by
    :mod:`rest_framework_services.views.spec_validation`. That is deliberate:
    ``metadata`` exists to be read by consumer code that may never mount the
    spec on a view at all (a registry consumer, :func:`dispatch_spec`, an MCP
    or Pydantic-AI binding), so a view-time check would skip exactly the paths
    the field is for.

    Shape-only by design. The framework never looks inside the mapping.
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

    Shared by every relation kind that reconciles a collection, so the two
    words mean the same thing on all of them: ``"replace"`` makes the incoming
    set authoritative and disposes of what it leaves out, ``"merge"`` upserts
    and removes nothing. What "dispose of" means is the kind's own business —
    a child row is unlinked or deleted, a many-to-many target is only dropped
    from the relation — but *when* it happens is this one flag.
    """
    if mode not in VALID_RELATION_MODES:
        raise ValueError(f"{label}.mode must be one of {VALID_RELATION_MODES}; got {mode!r}.")


def validate_relation_services(
    *,
    label: str,
    services: Mapping[str, Any],
    shaping: Mapping[str, Any],
) -> None:
    """Refuse a relation spec that declares a row service *and* row shaping.

    A ``create_service`` / ``update_service`` stands in for the mutation-helper
    call the spec would otherwise make, so every knob configuring that call —
    ``field_map``, ``exclude_fields``, ``m2m``, and the nested ``children`` /
    ``relations`` maps — is bypassed for that row. Left alone that is a *silent*
    failure: a spec declaring both a ``create_service`` and ``children`` writes
    no grandchildren at all and says nothing about it. So the combination is
    refused where every other spec contradiction is, at construction.

    The split is by ownership, not by field. Reconciliation stays with the spec
    whoever writes the row — ``fk``, ``match_key``, ``mode``, ``scope`` and the
    orphan rule are untouched by this check. ``delete_service`` is
    deliberately **not** a member of ``services``: it replaces the
    unlink-or-delete rule rather than the helper call, so it composes with row
    shaping (the cascade still removes a row's grandchildren before handing the
    row to the service). Refusing that pairing too would outlaw a combination
    that works.
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
