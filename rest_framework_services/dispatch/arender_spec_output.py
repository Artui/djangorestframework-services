"""``arender_spec_output`` — async sibling of
[`render_spec_output`][rest_framework_services.dispatch.render_spec_output.render_spec_output]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.dispatch.utils import arun_off_loop
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.view_hooks import ViewHooks


async def arender_spec_output(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    value: Any,
    *,
    many: bool = False,
    view: Any = None,
    request: Any = None,
    extras: Mapping[str, Any] | None = None,
    view_hooks: ViewHooks | None = None,
) -> Any:
    """Async
    [`render_spec_output`][rest_framework_services.dispatch.render_spec_output.render_spec_output].

    Identical arguments, identical result — the whole render runs in Django's
    thread-sensitive executor instead of on the event loop, which is what an
    async transport needs. Rendering is **full of** sync ORM work, none of it
    optional:

    - ``serializer.data`` iterates the value. For ``many=True`` that evaluates
      the queryset; per row, every relation a field traverses is another query
      unless it was ``select_related`` in.
    - The spec's ``output_serializer_context`` provider is user code, and is
      documented as the place to run one batched query keyed on the page.
    - The no-serializer path list-coerces, which evaluates a queryset too.

    So an async caller that awaited
    [`adispatch_spec`][rest_framework_services.dispatch.adispatch_spec.adispatch_spec] —
    which returns a ``LIST`` result as a **lazy** queryset, deliberately — cannot render
    it inline without raising ``SynchronousOnlyOperation``. Pair the two:

    ```python
    result = await adispatch_spec(spec, user=user, params=params)
    payload = await arender_spec_output(spec, result.value, many=result.kind == "list")
    ```

    A transport that already does its own thread hop around rendering (to paginate or
    post-process in the same hop) can keep calling
    [`render_spec_output`][rest_framework_services.dispatch.render_spec_output.render_spec_output]
    inside it — this is for the ordinary case, where the hop shouldn't have to be the
    caller's problem to remember.

    Pagination is still the caller's job; see the sync twin for the ``extras``
    contract and the rest of the semantics.
    """
    return await arun_off_loop(
        render_spec_output,
        spec,
        value,
        many=many,
        view=view,
        request=request,
        extras=extras,
        view_hooks=view_hooks,
    )


__all__ = ["arender_spec_output"]
