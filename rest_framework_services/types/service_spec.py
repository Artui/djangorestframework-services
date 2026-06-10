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
    cross-validate. Server-provided keys win on conflict. The provider is
    called with ``(view, request)`` positionally and may additionally
    declare ``instance`` as a keyword parameter to receive the resolved
    mutation target (``None`` on create) — passed only when declared, so
    pre-validation input mutation that depends on the current row has a
    home. The same declare-to-receive rule applies to the
    ``get_input_data`` / ``get_<action>_input_data`` view hooks.

    ``input_serializer_context`` is a per-spec hook for the input
    serializer's ``context=`` dict. It sits at the most-specific layer of
    the resolution chain (``view.get_serializer_context`` →
    ``view.get_input_serializer_context`` →
    ``view.get_<action>_input_serializer_context`` → spec hook), so the
    spec wins on overlapping keys. ``None`` (the default) leaves the
    three earlier layers intact. The symmetrical output hook lives on the
    nested ``output_selector_spec.output_serializer_context``; that hook may
    additionally declare a ``result`` keyword to receive the final
    (post-selector) instance being serialized — passed only when declared —
    so it can run a single batched query against it and propagate the
    outcome through context. The output hook always runs *after* the service
    and output selector have resolved ``result``.

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

    ``instance_selector_spec`` is the input-side twin of
    ``output_selector_spec``: a nested :class:`SelectorSpec` (``kind`` must
    be :attr:`SelectorKind.RETRIEVE`) that resolves the instance an
    update / destroy / detail action targets, embedding the lookup in the
    spec instead of relying on the view's ``queryset`` / ``get_object()``
    chain. The selector's kwarg pool is ``{request, user}`` plus the URL
    kwargs (plus the standard selector extras chain), so
    ``selector=lambda *, pk: Project.objects.filter(pk=pk)`` resolves the
    row from the route. Resolution happens **before** input validation —
    the resolved instance is handed to the input serializer
    (DRF-style ``serializer(instance, data=..., partial=...)``) and seeded
    into the service kwarg pool as ``instance``. A ``None`` / missing
    resolution raises :exc:`~rest_framework.exceptions.NotFound` (the
    nested spec's ``allow_none`` flag is ignored — an update against a
    missing row is always a 404), and object-level permissions
    (``check_object_permissions``) run against the resolved instance. The
    queryset-shaping fields apply; ``permission_classes``,
    ``output_serializer``, and ``output_serializer_context`` on the nested
    spec are ignored. ``None`` (the default) keeps today's ``get_object()``
    chain.

    ``partial`` overrides the transport-derived partial-validation flag.
    ``None`` (the default) inherits the flag the calling surface derives
    (``False`` for PUT/POST, ``True`` for PATCH); ``True`` / ``False``
    forces it regardless of HTTP method — e.g. ``partial=False`` on an
    ``action_specs["partial_update"]`` entry makes a PATCH endpoint
    enforce ``required`` fields like a PUT. Applied once, at
    ``dispatch_mutation_for_spec``, so it is honoured uniformly by the
    viewset mixins, the standalone views, and ``@service_action``.

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
    partial: bool | None = None

    # Input pipeline.
    input_serializer: type | None = None
    # ``input_data`` providers are called as ``(view, request)`` and may
    # additionally declare ``instance`` (the resolved mutation target,
    # ``None`` on create) as a keyword parameter — passed only when
    # declared; hence the open ``...`` parameter spec.
    input_data: Callable[..., Mapping[str, Any]] | None = None
    input_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    # Instance resolution for update / destroy / detail actions — the
    # input-side twin of ``output_selector_spec`` (kind=RETRIEVE).
    instance_selector_spec: SelectorSpec[Any, Any] | None = None

    # Output pipeline — the full re-fetch / serialize / shape group lives
    # inside a nested SelectorSpec (kind=RETRIEVE).
    output_selector_spec: SelectorSpec[Any, Any] | None = None

    # Cross-cutting.
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
