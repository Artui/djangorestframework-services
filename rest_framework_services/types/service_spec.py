"""``ServiceSpec`` — bundles per-action configuration for mutation actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from rest_framework_services.types.selector_spec import SelectorSpec
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
    - ``ResultT`` — the value returned by the service callable, and (when
      ``output_selector_spec`` is set) the input to its ``selector``.
    - ``ExtraT`` — a ``TypedDict`` describing the keys returned by ``kwargs``.

    All three default to ``Any``, so ``ServiceSpec(service=fn)`` keeps working
    unchanged.

    Fields are grouped by what they configure: the service callable itself,
    the input pipeline (``input_*``), the output pipeline (a single nested
    :class:`SelectorSpec`), and the cross-cutting concerns (``kwargs``,
    ``permission_classes``).

    ``success_status`` is left as ``None`` so each consumer can supply its
    own action-appropriate default (201 for create, 200 for update, 204
    for destroy).

    ``input_data`` is the symmetrical hook for the *serializer's* input.
    Returns a mapping merged on top of ``request.data`` before the
    ``input_serializer`` validates it — useful for lifting URL kwargs
    (e.g. parent IDs from nested routes) into fields the serializer can
    cross-validate. Server-provided keys win on conflict.

    ``input_serializer_context`` is a per-spec hook for the input
    serializer's ``context=`` dict. It sits at the most-specific layer of
    the resolution chain (``view.get_serializer_context`` →
    ``view.get_input_serializer_context`` →
    ``view.get_<action>_input_serializer_context`` → spec hook), so the
    spec wins on overlapping keys. ``None`` (the default) leaves the
    three earlier layers intact. The symmetrical output hook lives on the
    nested ``output_selector_spec.output_serializer_context``.

    ``output_selector_spec`` is the full output pipeline collapsed into a
    single :class:`SelectorSpec`. Its ``kind`` must be
    :attr:`SelectorKind.RETRIEVE` (the post-mutation re-fetch always
    materializes a single instance). Set it to render the response through
    a different shape than what the service returned (typical pattern: the
    service returns a freshly created/updated instance, the
    ``output_selector_spec.selector`` re-fetches it with the relations the
    response serializer needs, and the spec's ``output_serializer``
    renders the result). ``None`` (the default) means "render the service's
    return value directly". The nested spec's ``permission_classes`` and
    ``kwargs`` are ignored — the surrounding mutation's permissions and
    kwargs chain apply.

    ``kwargs`` is a callable that returns extra kwargs to merge into the
    pool the service receives. Co-locating it with the spec lets each action
    declare its own contract — no ``if self.action == ...`` branching in a
    catch-all ``get_service_kwargs``. See :class:`ServiceView` for the
    attributes available on the ``view`` argument.

    ``permission_classes`` overrides the calling view's ``permission_classes``
    for the action the spec backs. ``None`` (the default) means "inherit the
    view's class-level permissions"; an empty sequence means "no permissions"
    explicitly. Forwarded through DRF's ``@action(permission_classes=...)``
    for the ``@service_action`` decorator, and surfaced via ``get_permissions``
    for the viewset mixins and standalone views.
    """

    # The service callable and per-call dispatch flags.
    service: Callable[..., ResultT]
    atomic: bool = True
    success_status: int | None = None

    # Input pipeline.
    input_serializer: type | None = None
    input_data: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    input_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None

    # Output pipeline — the full re-fetch / serialize / shape group lives
    # inside a nested SelectorSpec (kind=RETRIEVE).
    output_selector_spec: SelectorSpec[Any, Any] | None = None

    # Cross-cutting.
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
