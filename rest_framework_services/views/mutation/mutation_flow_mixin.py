"""``MutationFlowMixin`` — shared service-action flow for views and viewsets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.utils import dispatch_mutation_for_spec


class MutationFlowMixin:
    """Provides ``_run_mutation`` for service-backed views and viewset mixins.

    The flow itself lives in :func:`dispatch_mutation_for_spec`, so
    ``@service_action`` can reach it without being a class; this mixin is the OO
    entry point that the per-action mixins (``ServiceCreateMixin`` and friends)
    and the standalone single-purpose views compose, calling
    ``self._run_mutation(...)`` after resolving their per-action spec.

    Four hook chains feed a mutation, each layered view-wide → per-action →
    per-spec and merged with ``dict.update`` so the more specific hook wins on
    overlapping keys:

    - extra service kwargs: ``get_service_kwargs`` →
      ``get_<action>_service_kwargs`` → :attr:`ServiceSpec.kwargs`.
    - the *serializer's* input dict, merged on top of ``request.data`` before
      validation: ``get_input_data`` → ``get_<action>_input_data`` →
      :attr:`ServiceSpec.input_data`.
    - the input serializer's ``context=``: ``get_serializer_context`` (DRF's
      own) → ``get_input_serializer_context`` →
      ``get_<action>_input_serializer_context`` →
      :attr:`ServiceSpec.input_serializer_context`.
    - the output serializer's ``context=``: the same chain with ``output`` in
      place of ``input``, applied during response rendering.

    The per-action layer reads ``self.action``, so it applies to viewsets only.
    """

    def get_service_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to every mutation service."""
        return {}

    def get_input_data(self, request: Request) -> Mapping[str, Any]:
        """Hook for extras merged on top of ``request.data`` before validation."""
        return {}

    def get_input_serializer_context(self) -> dict[str, Any]:
        """Hook for the ``context=`` dict passed to the *input* serializer.

        Defaults to :meth:`get_serializer_context`, so overriding the
        DRF-standard hook flows into input validation automatically; override
        here for keys visible only during input validation.
        """
        return self.get_serializer_context()  # ty: ignore[unresolved-attribute]

    def get_output_serializer_context(self) -> dict[str, Any]:
        """Hook for the ``context=`` dict passed to the *output* serializer.

        Defaults to :meth:`get_serializer_context`, so overriding the
        DRF-standard hook flows into response rendering automatically; override
        here for keys visible only during response rendering.
        """
        return self.get_serializer_context()  # ty: ignore[unresolved-attribute]

    def get_permissions(self) -> list[Any]:
        """Honor ``spec.permission_classes`` on standalone mutation views.

        Standalone ``Service*View`` subclasses carry ``spec`` as a class
        attribute; when it sets ``permission_classes`` those win over the view's
        class-level ones, and ``None`` falls through.
        """
        spec: ServiceSpec[Any, Any, Any] | None = getattr(self, "spec", None)
        if spec is not None and spec.permission_classes is not None:
            return [permission() for permission in spec.permission_classes]
        return super().get_permissions()  # ty: ignore[unresolved-attribute]

    def _run_mutation(
        self,
        request: Request,
        spec: ServiceSpec[Any, Any, Any],
        *,
        instance: Any,
        default_status: int,
        render_instance_on_none: bool = False,
        partial: bool = False,
    ) -> Response:
        return dispatch_mutation_for_spec(
            self,
            request,
            spec,
            instance=instance,
            default_status=default_status,
            render_instance_on_none=render_instance_on_none,
            partial=partial,
        )
