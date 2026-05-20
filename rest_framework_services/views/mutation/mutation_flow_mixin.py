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

    The actual flow lives in :func:`dispatch_mutation_for_spec` (so
    ``@service_action`` can reach it without being a class). This mixin
    is the OO entry point: the per-action mixins (``ServiceCreateMixin``
    etc.) and the standalone single-purpose views compose it and call
    ``self._run_mutation(...)`` after resolving their per-action spec.

    Three layers contribute extra kwargs to every service call (most specific
    wins):

    1. ``get_service_kwargs(self)`` — global fallback on the view.
    2. ``get_<action>_service_kwargs(self)`` — per-action override
       (viewsets only; ``self.action`` must be set).
    3. ``ServiceSpec.kwargs`` — per-spec callable, co-located with the
       service it feeds.

    A symmetrical three-layer chain feeds the *serializer's* input dict
    (merged on top of ``request.data`` before validation):

    1. ``get_input_data(self, request)`` — global fallback on the view.
    2. ``get_<action>_input_data(self, request)`` — per-action override.
    3. ``ServiceSpec.input_data`` — per-spec callable.

    Two further three-layer chains feed the ``context=`` argument passed to
    the input serializer (during validation) and the output serializer
    (during response rendering):

    1. ``get_serializer_context(self)`` — DRF's default
       (request, view, format).
    2. ``get_input_serializer_context(self)`` /
       ``get_output_serializer_context(self)`` — directional fallback;
       defaults to ``self.get_serializer_context()`` so a single
       ``get_serializer_context`` override flows into both directions.
    3. ``get_<action>_input_serializer_context(self)`` /
       ``get_<action>_output_serializer_context(self)`` — per-action
       override (viewsets only; ``self.action`` must be set).

    Each layer's result is merged with ``dict.update`` so the more specific
    hook wins on overlapping keys.
    """

    def get_service_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to every mutation service."""
        return {}

    def get_input_data(self, request: Request) -> Mapping[str, Any]:
        """Hook for extras merged on top of ``request.data`` before validation."""
        return {}

    def get_input_serializer_context(self) -> dict[str, Any]:
        """Hook for the ``context=`` dict passed to the *input* serializer.

        Defaults to :meth:`get_serializer_context` so overriding the
        DRF-standard hook flows into the input-validation path automatically.
        Override here to inject keys visible only during input validation.
        """
        return self.get_serializer_context()  # ty: ignore[unresolved-attribute]

    def get_output_serializer_context(self) -> dict[str, Any]:
        """Hook for the ``context=`` dict passed to the *output* serializer.

        Defaults to :meth:`get_serializer_context` so overriding the
        DRF-standard hook flows into the response-rendering path
        automatically. Override here to inject keys visible only during
        response rendering.
        """
        return self.get_serializer_context()  # ty: ignore[unresolved-attribute]

    def get_permissions(self) -> list[Any]:
        """Honor ``spec.permission_classes`` on standalone mutation views.

        Standalone ``Service*View`` subclasses carry ``spec`` as a class
        attribute. When the spec sets ``permission_classes`` it wins over
        the view's class-level ``permission_classes``; ``None`` (the default)
        falls through.
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
        success_status: int,
        partial: bool = False,
    ) -> Response:
        return dispatch_mutation_for_spec(
            self,
            request,
            spec,
            instance=instance,
            success_status=success_status,
            partial=partial,
        )
