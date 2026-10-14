"""``adispatch_spec`` - async sibling of
[`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async
from django_service_specs.dispatch.adispatch import adispatch

from rest_framework_services.dispatch.utils import edge_errors, listed_output, prepare_edge_call
from rest_framework_services.service_specs.register_drf_row_errors import (
    register_drf_row_errors,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks

# As in ``dispatch_spec``: no ``AppConfig`` to register from, and an async
# caller may import this module alone. Idempotent.
register_drf_row_errors()


async def adispatch_spec(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    user: Any,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    params: Mapping[str, Any] | list[Any],
    request: Any = None,
    view: Any = None,
    success_status: int | None = None,
    argument_binding: ArgumentBinding = ArgumentBinding.AUTO,
    unknown_arguments: UnknownArguments = UnknownArguments.IGNORE,
    on_target_resolved: TargetGuard | None = None,
    progress: ProgressReporter | None = None,
    view_hooks: ViewHooks | None = None,
    instance: Any = UNSET,
    filter_data: Mapping[str, Any] | None = None,
    many_as_argument: bool = False,
    partial: bool | None = None,
) -> DispatchResult:
    """Async
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]:
    the same arguments, the same policies and the same result.

    The call is prepared exactly as the sync twin prepares it, in one hop to
    Django's thread-sensitive executor: compiling the spec's variant, routing
    the arguments, resolving a service spec's target and running the
    ``on_target_resolved`` guard all happen there, since the lookup and the
    guard query. Then the kernel's ``adispatch`` runs the call, and its own
    rule for what runs off the loop applies to everything after: a sync
    selector, service, provider, precondition or affordance condition runs in
    the executor, an ``async def`` one is awaited. A selector spec's guard runs
    as a precondition there.

    A ``LIST`` result comes back as the lazy shaped queryset, for the async
    transport to materialize or paginate in a thread
    ([`arender_spec_output`][rest_framework_services.dispatch.arender_spec_output.arender_spec_output]
    does both).
    """
    with edge_errors(spec, params, many_as_argument=many_as_argument):
        prepared = await sync_to_async(prepare_edge_call, thread_sensitive=True)(
            spec,
            user=user,
            pool_seeds=pool_seeds,
            params=params,
            request=request,
            view=view,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=on_target_resolved,
            progress=progress,
            view_hooks=view_hooks,
            instance=instance,
            filter_data=filter_data,
            many_as_argument=many_as_argument,
            partial=partial,
        )
        if isinstance(prepared, DispatchResult):
            return prepared
        result = await adispatch(prepared.spec, **prepared.kwargs)
    return listed_output(spec, result)


__all__ = ["adispatch_spec"]
