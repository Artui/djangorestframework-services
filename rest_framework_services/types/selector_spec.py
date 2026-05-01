"""``SelectorSpec`` — bundles per-action configuration for read actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from rest_framework.request import Request
from rest_framework.serializers import Serializer

from rest_framework_services.types.service_view import ServiceView

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])


@dataclass(frozen=True)
class SelectorSpec(Generic[ResultT, ExtraT]):
    """All wiring for a single read action in one record.

    Used as a value in ``action_specs`` on viewsets and as the ``spec=``
    argument to :class:`SelectorListView` / :class:`SelectorRetrieveView`.

    Generic parameters (both default to ``Any``):

    - ``ResultT`` — the selector's return type.
    - ``ExtraT`` — a ``TypedDict`` describing the keys returned by ``kwargs``.

    Fields:

    - **``selector``** — callable invoked by ``get_queryset()`` (list) or
      ``get_object()`` (retrieve). ``None`` means "use the configured
      ``queryset`` / default DRF behaviour".
    - **``output_serializer``** — DRF ``Serializer`` subclass used by
      ``get_serializer_class()`` for this action. ``None`` falls back to
      DRF's standard ``serializer_class``.
    - **``kwargs``** — callable returning extra kwargs to merge into the pool
      the selector receives. Co-locating it with the spec lets each action
      declare its own contract — no ``if self.action == ...`` branching in a
      catch-all ``get_selector_kwargs``.
    """

    selector: Callable[..., ResultT] | None = None
    output_serializer: type[Serializer] | None = None
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
