"""``build_offline_context`` — synthesize the request / view a spec needs off-HTTP."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.http import HttpRequest, QueryDict
from rest_framework.request import Request

from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_service_view import OfflineServiceView


def build_offline_context(
    user: Any,
    params: Mapping[str, Any] | list[Any] | None = None,
    *,
    http_request: HttpRequest | None = None,
    action: str | None = None,
    kwargs: Mapping[str, Any] | None = None,
    query_params: Mapping[str, Any] | None = None,
) -> OfflineContext:
    """Build the :class:`OfflineContext` for dispatching a spec outside an HTTP request.

    :func:`~rest_framework_services.dispatch_spec` forwards ``request`` / ``view``
    to spec callables (``kwargs`` providers, ``extend_queryset``, context
    providers) that declare them, and :func:`~rest_framework_services.enforce_permissions`
    needs a request + view to evaluate ``permission_classes``. This synthesizes
    both so a spec written for the HTTP transport keeps working when driven from
    a Pydantic-AI toolset, the MCP server, a management command, or a task runner.

    - ``user`` is set on the synthetic request (``request.user``) so callables
      and permissions that read it behave as on HTTP.
    - ``params`` seeds ``request.data`` for callables that read it. It is *not*
      validated here — that is :func:`dispatch_spec`'s job, which takes ``params``
      directly and never touches ``request.data``. Pass the same value to both.
      Seeded into DRF's parsed-data cache directly: ``params`` is already
      structured, so there is nothing to parse and a synthetic request has no
      WSGI stream to read.
    - ``http_request`` is wrapped when supplied (e.g. the MCP server passes its
      real Django request so headers / ``META`` are available); otherwise a bare
      :class:`~django.http.HttpRequest` is created. The method is forced to
      ``POST`` because mutation callables often branch on it.
    - ``query_params`` seeds the request's ``GET`` :class:`~django.http.QueryDict`
      — the source ``request.query_params`` reads. This is how read-shaping params
      that aren't spec inputs reach the serializer over the offline path:
      drf-services' own ``SelectorSpec.filter_set`` (otherwise dead off-HTTP), and
      any serializer that branches on ``request.query_params`` (django-restql field
      selection, custom serializers). Values are stringified as on HTTP; a list /
      tuple value becomes a multi-valued param (``getlist``). Defaults to empty →
      no behaviour change. When both ``query_params`` and ``http_request`` are
      given, this **replaces** the wrapped request's ``GET``.
    - ``action`` / ``kwargs`` populate the :class:`OfflineServiceView`.
      ``kwargs`` is the off-HTTP counterpart of a view's URL captures (the
      ``parent_pk`` of a nested route): it is **the** channel for route-derived
      values, and :func:`dispatch_spec` spreads it into the selector / target
      pools exactly where the HTTP path spreads ``extra_url_kwargs=view.kwargs``
      — authoritative over ``params`` on a key conflict, below a ``spec.kwargs``
      provider. A ``spec.kwargs`` provider that reads ``view.kwargs`` (e.g. to
      scope by tenant) also sees it here. Pass every route capture a spec depends
      on; it defaults to ``{}`` (no URL context).
    """
    base: HttpRequest = http_request if http_request is not None else HttpRequest()
    base.method = "POST"
    if query_params is not None:
        # ``_build_query_dict`` freezes the result (``_mutable = False``), so it is
        # the immutable ``GET`` the stub's type wants; the stub can't see that.
        base.GET = _build_query_dict(query_params)  # ty: ignore[invalid-assignment]
    # The django-stubs ``HttpRequest.__new__`` bleeds into the ``Request``
    # subclass, so ty resolves the wrong overload and rejects the call.
    # Construct via ``Any`` and cast back to keep the static type on the result.
    raw: Any = Request(base)  # ty: ignore[too-many-positional-arguments]
    drf_request: Request = cast(Request, raw)
    drf_request.user = user
    # ``_full_data`` is the cache DRF's ``request.data`` property returns; seeding
    # it bypasses parsing entirely (there is no stream on a synthetic request).
    drf_request._full_data = params if params is not None else {}  # ty: ignore[unresolved-attribute]
    view = OfflineServiceView(
        request=drf_request, action=action, kwargs=dict(kwargs) if kwargs is not None else {}
    )
    return OfflineContext(user=user, request=drf_request, view=view)


def _build_query_dict(query_params: Mapping[str, Any]) -> QueryDict:
    """Build an immutable ``QueryDict`` from a mapping, mirroring an HTTP ``GET``.

    Scalars are stringified (query params are always strings on the wire); a
    list / tuple value becomes a multi-valued param so ``getlist`` sees each item.
    The result is frozen (``_mutable = False``) like a real request's ``GET``.
    """
    query_dict = QueryDict(mutable=True)
    for key, value in query_params.items():
        if isinstance(value, (list, tuple)):
            query_dict.setlist(key, [str(item) for item in value])
        else:
            query_dict[key] = str(value)
    query_dict._mutable = False
    return query_dict
