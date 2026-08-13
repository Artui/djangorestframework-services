"""``build_offline_context`` — synthesize the request / view a spec needs off-HTTP."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit

from django.http import HttpRequest, QueryDict
from rest_framework.request import Request

from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_http_request import OfflineHttpRequest
from rest_framework_services.types.offline_service_view import OfflineServiceView


def build_offline_context(
    user: Any,
    params: Mapping[str, Any] | list[Any] | None = None,
    *,
    http_request: HttpRequest | None = None,
    action: str | None = None,
    kwargs: Mapping[str, Any] | None = None,
    query_params: Mapping[str, Any] | None = None,
    host: str | None = None,
) -> OfflineContext:
    """Build the
    [`OfflineContext`][rest_framework_services.types.offline_context.OfflineContext] for
    dispatching a spec outside an HTTP request.

    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]
    forwards ``request`` / ``view`` to spec callables (``kwargs`` providers,
    ``extend_queryset``, context providers) that declare them, and
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions]
    needs a request + view to evaluate ``permission_classes``. This synthesizes both so
    a spec written for the HTTP transport keeps working when driven from a Pydantic-AI
    toolset, the MCP server, a management command, or a task runner.

    Args:
        user: Set as ``request.user``, so callables and permissions that read it behave
            as on HTTP.
        params: Seeds ``request.data``, straight into DRF's parsed-data cache (a
            synthetic request has no stream to parse). It is *not* validated here — that
            is
            [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]'s
            job, which takes ``params`` directly and never touches ``request.data``.
            Pass both the same value.
        http_request: An ambient Django request to wrap, so its headers / ``META`` are
            available (the MCP server passes its real one); otherwise an
            [`OfflineHttpRequest`][rest_framework_services.types.offline_http_request.OfflineHttpRequest]
            is created. Either way the method is forced to ``POST``, because mutation
            callables often branch on it.
        action: The view action name, exposed on the
            [`OfflineServiceView`][rest_framework_services.types.offline_service_view.OfflineServiceView].
        kwargs: **The** channel for route-derived values — the off-HTTP counterpart of a
            view's URL captures.
            [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]
            spreads it into the selector / target pools exactly where the HTTP path
            spreads ``extra_url_kwargs=view.kwargs``: authoritative over ``params`` on a
            key conflict, below a ``spec.kwargs`` provider, which also sees it as
            ``view.kwargs``. Pass every route capture a spec depends on.
        query_params: Seeds the request's ``GET`` ``QueryDict``, the source
            ``request.query_params`` reads — how read-shaping params that are not spec
            inputs reach the serializer offline (``SelectorSpec.filter_set``, a
            serializer branching on ``query_params``). Values are stringified as on
            HTTP; a list / tuple becomes a multi-valued param. **Replaces** a wrapped
            ``http_request``'s ``GET``.
        host: The origin the synthesized request reports, so ``build_absolute_uri`` —
            which DRF's ``FileField`` / ``HyperlinkedIdentityField`` call whenever a
            ``request`` is in the serializer context — returns real absolute URLs off
            HTTP. Accepts ``"example.com"``, ``"example.com:8000"``, or a full origin
            whose scheme decides whether links are https. There is no default: unset,
            absolute-URI building degrades to the relative URL rather than raising (see
            [`OfflineHttpRequest`][rest_framework_services.types.offline_http_request.OfflineHttpRequest]).
            **Ignored when ``http_request`` is supplied**, whose own headers are
            authoritative, so a caller can pass both unconditionally.

    Returns: The
        [`OfflineContext`][rest_framework_services.types.offline_context.OfflineContext]
        to hand to ``dispatch_spec``.
    """
    base: HttpRequest = http_request if http_request is not None else _synthesize_request(host)
    base.method = "POST"
    if query_params is not None:
        # ``_build_query_dict`` freezes the result (``_mutable = False``), so it is
        # the immutable ``GET`` the stub's type wants; the stub can't see that.
        base.GET = _build_query_dict(query_params)  # ty: ignore[invalid-assignment]
    # Constructed via ``Any`` and cast back to keep the static type on the result.
    raw: Any = Request(base)
    drf_request: Request = cast(Request, raw)
    drf_request.user = user
    # ``_full_data`` is the cache DRF's ``request.data`` property returns; seeding
    # it bypasses parsing entirely (there is no stream on a synthetic request).
    drf_request._full_data = params if params is not None else {}  # ty: ignore[unresolved-attribute]
    view = OfflineServiceView(
        request=drf_request, action=action, kwargs=dict(kwargs) if kwargs is not None else {}
    )
    return OfflineContext(user=user, request=drf_request, view=view)


def _synthesize_request(host: str | None) -> OfflineHttpRequest:
    """Build the hostless-by-default stand-in, seeding ``META`` when a host is given.

    The ``META`` keys mirror what a WSGI server would set. Host and scheme resolution
    itself is
    [`OfflineHttpRequest`][rest_framework_services.types.offline_http_request.OfflineHttpRequest]'s.
    """
    # ``HttpRequest.__new__`` in django-stubs returns ``_MutableHttpRequest``, so
    # ty resolves the subclass to the wrong type; construct via ``Any`` and cast
    # back (the same stub quirk this module works around for ``Request``).
    raw: Any = OfflineHttpRequest()
    request: OfflineHttpRequest = cast(OfflineHttpRequest, raw)
    if host is None:
        return request
    scheme, netloc = _split_host(host)
    hostname, _, port = netloc.partition(":")
    request.offline_host = netloc
    request.META["HTTP_HOST"] = netloc
    request.META["SERVER_NAME"] = hostname
    request.META["SERVER_PORT"] = port or ("443" if scheme == "https" else "80")
    request.META["wsgi.url_scheme"] = scheme
    return request


def _split_host(host: str) -> tuple[str, str]:
    """Split ``host`` into ``(scheme, netloc)``, defaulting the scheme to ``http``.

    Django's own default for a request with no TLS marker, and the safe one: a
    wrong ``https://`` is a broken link, and a caller wanting https says so by
    passing a full origin.
    """
    if "//" not in host:
        return ("http", host.strip("/"))
    split = urlsplit(host)
    return (split.scheme or "http", split.netloc)


def _build_query_dict(query_params: Mapping[str, Any]) -> QueryDict:
    """Build an immutable ``QueryDict`` from a mapping, mirroring an HTTP ``GET``.

    Scalars are stringified (query params are always strings on the wire); a
    list / tuple becomes a multi-valued param so ``getlist`` sees each item.
    Frozen like a real request's ``GET``.
    """
    query_dict = QueryDict(mutable=True)
    for key, value in query_params.items():
        if isinstance(value, (list, tuple)):
            query_dict.setlist(key, [str(item) for item in value])
        else:
            query_dict[key] = str(value)
    query_dict._mutable = False
    return query_dict
