"""Resolve a :class:`PolymorphicServiceSpec` to its chosen concrete spec."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.map_service_error import map_service_error
from rest_framework_services.views.utils import resolve_callable_kwargs

_CACHE_ATTR = "_rfs_polymorphic_choices"


def resolve_polymorphic_service_spec(
    poly: PolymorphicServiceSpec, *, view: Any, request: Any
) -> ServiceSpec[Any, Any, Any]:
    """Run the discriminator and return the chosen variant ``ServiceSpec``.

    The discriminator resolves through the keyword pool
    ``{request, data, user, view}`` (``data`` is the *raw* ``request.data``) and
    its result is memoized on ``view`` keyed by ``id(poly)``, since dispatch,
    ``get_permissions`` and serializer resolution may each resolve the same
    spec. A key absent from ``specs`` is :exc:`ImproperlyConfigured`; a
    ``ServiceError`` the discriminator raises to reject a payload is mapped to
    its DRF equivalent here, because this runs ahead of the mutation flow's own
    error mapping.
    """
    cache: dict[int, ServiceSpec[Any, Any, Any]] | None = getattr(view, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(view, _CACHE_ATTR, cache)
    cache_key = id(poly)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    pool: dict[str, Any] = {
        "request": request,
        "data": getattr(request, "data", None),
        "user": getattr(request, "user", None),
        "view": view,
    }
    try:
        variant_key = poly.discriminator(**resolve_callable_kwargs(poly.discriminator, pool))
    except ServiceError as exc:
        raise map_service_error(exc) from exc
    try:
        chosen = poly.specs[variant_key]
    except KeyError:
        raise ImproperlyConfigured(
            f"PolymorphicServiceSpec discriminator returned {variant_key!r}, which is not a "
            f"configured variant (have {sorted(poly.specs)})."
        ) from None
    cache[cache_key] = chosen
    return chosen
