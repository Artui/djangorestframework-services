"""Resolve a :class:`PolymorphicServiceSpec` to its chosen concrete spec."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.map_service_error import map_service_error
from rest_framework_services.views.utils import resolve_callable_kwargs

# Per-request cache stashed on the view instance so the discriminator runs once.
_CACHE_ATTR = "_rfs_polymorphic_choices"


def resolve_polymorphic_service_spec(
    poly: PolymorphicServiceSpec, *, view: Any, request: Any
) -> ServiceSpec[Any, Any, Any]:
    """Run the discriminator and return the chosen variant ``ServiceSpec``.

    The discriminator is resolved through the framework keyword pool
    ``{request, data, user, view}`` (``data`` is the *raw* ``request.data``).
    The result is memoized on the ``view`` instance keyed by ``id(poly)``, so
    the discriminator runs at most once per request even though dispatch,
    ``get_permissions`` (the ``discriminate`` strategy), and serializer
    resolution may each resolve the same spec.

    A discriminator that returns a key absent from ``specs`` is a configuration
    error (:exc:`ImproperlyConfigured`). A *rejected* payload is the
    discriminator's own responsibility to raise a
    :exc:`~rest_framework_services.exceptions.service_error.ServiceError` on;
    because the discriminator runs before the mutation flow's own error
    mapping, that ``ServiceError`` is translated to its DRF equivalent here
    (``ServiceValidationError`` → 400, else 422).
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
