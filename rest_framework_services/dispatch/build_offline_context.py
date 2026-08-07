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
      real Django request so headers / ``META`` are available); otherwise an
      :class:`~rest_framework_services.OfflineHttpRequest` is created. The method
      is forced to ``POST`` because mutation callables often branch on it.
    - ``host`` gives the synthesized request an origin, so ``build_absolute_uri``
      — which DRF's ``FileField`` / ``HyperlinkedIdentityField`` call whenever a
      ``request`` is in the serializer context — returns real absolute URLs off
      the HTTP path. Accepts ``"example.com"``, ``"example.com:8000"``, or a full
      origin like ``"https://example.com"`` (the scheme sets whether links are
      https). There is no default: only the project knows its public origin, and
      guessing one — the first ``ALLOWED_HOSTS`` entry, say, which is an
      *authorization* list and is routinely a wildcard or an internal load-balancer
      name — would emit confidently-wrong links. Left unset, absolute-URI building
      degrades to returning the relative URL rather than raising; see
      :class:`~rest_framework_services.OfflineHttpRequest`. **Ignored when
      ``http_request`` is supplied** — a real request's own headers are
      authoritative (configure Django's ``USE_X_FORWARDED_HOST`` if it sits behind
      a proxy), which lets a caller pass both unconditionally: the ambient request
      when there is one, this host when there isn't.
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
    base: HttpRequest = http_request if http_request is not None else _synthesize_request(host)
    base.method = "POST"
    if query_params is not None:
        # ``_build_query_dict`` freezes the result (``_mutable = False``), so it is
        # the immutable ``GET`` the stub's type wants; the stub can't see that.
        base.GET = _build_query_dict(query_params)  # ty: ignore[invalid-assignment]
    # Constructed via ``Any`` and cast back to keep the static type on the
    # result. This used to carry a ``ty: ignore[too-many-positional-arguments]``
    # for a django-stubs overload that bled into ``Request``; ty 0.0.65 resolves
    # it correctly and now flags the directive as unused.
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

    The ``META`` keys mirror what a WSGI server would set, so anything reading
    them directly (logging, a middleware-shaped helper) sees the familiar shape.
    Host and scheme resolution itself is
    :class:`~rest_framework_services.OfflineHttpRequest`'s — notably *without* the
    ``ALLOWED_HOSTS`` check, which guards against spoofed client headers and has
    nothing to guard here.
    """
    # ``HttpRequest.__new__`` in django-stubs returns ``_MutableHttpRequest``, so
    # ty resolves the subclass to the wrong type; construct via ``Any`` and cast
    # back (the same stub quirk this module already works around for ``Request``).
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
    wrong ``https://`` is a broken link, whereas a caller that wants https says so
    by passing a full origin.
    """
    if "//" not in host:
        return ("http", host.strip("/"))
    split = urlsplit(host)
    return (split.scheme or "http", split.netloc)


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
