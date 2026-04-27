"""``ServiceSpec`` — bundles per-action configuration for mutation actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rest_framework.serializers import Serializer


@dataclass(frozen=True)
class ServiceSpec:
    """All wiring for a single mutation action in one record.

    Used as a value in ``ServiceViewSet.service_specs`` and as the ``spec=``
    argument to :func:`service_action` / :class:`ServiceCreateView` /
    :class:`ServiceUpdateView` / :class:`ServiceDeleteView`.

    Fields mirror the kwargs that ``MutationFlowMixin._run_mutation``
    forwards to the underlying flow runner. ``success_status`` is left as
    ``None`` so each consumer can supply its own action-appropriate default
    (201 for create, 200 for update, 204 for destroy).
    """

    service: Callable[..., Any]
    input_serializer: type | None = None
    output_serializer: type[Serializer] | None = None
    output_selector: Callable[..., Any] | None = None
    atomic: bool = True
    success_status: int | None = None
