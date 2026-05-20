"""``ServiceSpec`` — bundles per-action configuration for mutation actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.serializers import Serializer

from rest_framework_services.types.service_view import ServiceView

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])


@dataclass(frozen=True)
class ServiceSpec(Generic[InputT, ResultT, ExtraT]):
    """All wiring for a single mutation action in one record.

    Used as a value in ``ServiceViewSet.action_specs`` and as the ``spec=``
    argument to :func:`service_action` / :class:`ServiceCreateView` /
    :class:`ServiceUpdateView` / :class:`ServiceDeleteView`.

    Generic parameters are optional and purely informational for type checkers:

    - ``InputT`` — the validated-data type produced by ``input_serializer``.
      For dataclass-based serializers this is the dataclass; for plain
      ``ModelSerializer`` it is typically ``dict[str, Any]``.
    - ``ResultT`` — the value returned by the service callable, the input to
      ``output_selector`` (when set), and the value rendered by
      ``output_serializer``.
    - ``ExtraT`` — a ``TypedDict`` describing the keys returned by ``kwargs``.

    All three default to ``Any``, so ``ServiceSpec(service=fn)`` keeps working
    unchanged.

    Fields mirror the kwargs that ``MutationFlowMixin._run_mutation``
    forwards to the underlying flow runner. ``success_status`` is left as
    ``None`` so each consumer can supply its own action-appropriate default
    (201 for create, 200 for update, 204 for destroy).

    ``kwargs`` is a callable that returns extra kwargs to merge into the
    pool the service receives. Co-locating it with the spec lets each action
    declare its own contract — no ``if self.action == ...`` branching in a
    catch-all ``get_service_kwargs``. See :class:`ServiceView` for the
    attributes available on the ``view`` argument.

    ``input_data`` is the symmetrical hook for the *serializer's* input.
    Returns a mapping merged on top of ``request.data`` before the
    ``input_serializer`` validates it — useful for lifting URL kwargs
    (e.g. parent IDs from nested routes) into fields the serializer can
    cross-validate. Server-provided keys win on conflict.

    ``permission_classes`` overrides the calling view's ``permission_classes``
    for the action the spec backs. ``None`` (the default) means "inherit the
    view's class-level permissions"; an empty sequence means "no permissions"
    explicitly. Forwarded through DRF's ``@action(permission_classes=...)``
    for the ``@service_action`` decorator, and surfaced via ``get_permissions``
    for the viewset mixins and standalone views.

    ``input_serializer_context`` and ``output_serializer_context`` are
    per-spec hooks for the serializer ``context=`` dict. They sit at the
    most-specific layer of the resolution chain
    (``view.get_serializer_context`` →
    ``view.get_<direction>_serializer_context`` →
    ``view.get_<action>_<direction>_serializer_context`` → spec hook), so
    the spec wins on overlapping keys. ``None`` (the default) leaves the
    three earlier layers intact.
    """

    service: Callable[..., ResultT]
    input_serializer: type | None = None
    output_serializer: type[Serializer] | None = None
    output_selector: Callable[..., Any] | None = None
    atomic: bool = True
    success_status: int | None = None
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    input_data: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    input_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    output_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
