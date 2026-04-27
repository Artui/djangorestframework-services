"""``MutationFlowMixin`` — shared service-action flow for views and viewsets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from rest_framework_services.views.mutation.utils import _execute_mutation


class MutationFlowMixin:
    """Provides ``_run_mutation`` for service-backed views and viewset mixins.

    The actual flow lives in the private :func:`_execute_mutation` helper
    (so ``@service_action`` can reach it without being a class). This mixin
    is the OO entry point: the per-action mixins (``ServiceCreateMixin``
    etc.) and the standalone single-purpose views compose it and call
    ``self._run_mutation(...)`` after resolving their per-action config.

    Override ``get_service_kwargs`` to inject extras into every service
    invocation on the composed view/viewset.
    """

    def get_service_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to every mutation service."""
        return {}

    def _run_mutation(
        self,
        request: Request,
        *,
        service: Callable[..., Any],
        input_serializer: type | None,
        output_serializer: type[Serializer] | None,
        output_selector: Callable[..., Any] | None,
        atomic: bool,
        success_status: int,
        instance: Any,
        partial: bool = False,
    ) -> Response:
        return _execute_mutation(
            self,
            request,
            service=service,
            input_serializer=input_serializer,
            output_serializer=output_serializer,
            output_selector=output_selector,
            atomic=atomic,
            success_status=success_status,
            instance=instance,
            extra_kwargs=self.get_service_kwargs(),
            partial=partial,
        )
