"""``PolymorphicServiceSpec`` — one action, N mutually exclusive payload shapes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from rest_framework_services.types.service_spec import ServiceSpec

# How ``get_permissions`` behaves for a polymorphic action (see the field docs).
PermissionStrategy = Literal["union", "discriminate", "require_identical"]


@dataclass(frozen=True)
class PolymorphicServiceSpec:
    """A single action that accepts several mutually exclusive payload shapes.

    Each variant has its own input serializer and service, bundled as a full
    :class:`ServiceSpec` under a string key. A ``discriminator`` callable
    inspects the request and returns the key; dispatch then proceeds through the
    chosen spec exactly as a plain ``ServiceSpec`` would. Usable anywhere a
    ``ServiceSpec`` is: an ``action_specs`` entry and the ``spec=`` of
    ``@service_action``.

    ```python
    PolymorphicServiceSpec(
        discriminator=resolve_flow,        # pool: {request, data, user, view} → key
        specs={"email": email_spec, "token": token_spec},
    )
    ```

    ``discriminator`` is resolved through the framework keyword pool — it
    declares any subset of ``request`` / ``data`` (the *raw* ``request.data``) /
    ``user`` / ``view`` (or ``**kwargs``) — and returns a key present in
    ``specs``. For a no-match / rejected payload it should **raise**
    (e.g. :exc:`~rest_framework_services.exceptions.service_validation_error.ServiceValidationError`,
    which the view layer maps to a 400); returning a key absent from ``specs``
    is a configuration error and raises :exc:`~django.core.exceptions.ImproperlyConfigured`.

    ``permission_strategy`` decides how ``get_permissions`` behaves, since DRF
    runs permissions before the body is necessarily parsed:

    - ``"union"`` (the default) — require the union of every variant's
      ``permission_classes``; the request must satisfy them all. No early body
      read, and the conservative/secure failure mode (a mis-declared
      discriminator branch can't *widen* access). For the common "same auth,
      different payload shapes" case this is identical to ``"require_identical"``.
    - ``"discriminate"`` — run the discriminator early (reading the raw body)
      and apply only the chosen variant's ``permission_classes``. Most precise,
      for the genuine "each variant is a different privilege" case; accepts the
      early-body-read tradeoff.
    - ``"require_identical"`` — validated at ``as_view()`` time to require every
      variant to declare the same ``permission_classes``; sidesteps ordering
      entirely.

    The resolved concrete spec flows through the shared action→spec chain, so
    the chosen variant's serializer context, kwargs, and output pipeline all
    apply — the discriminator is resolved once per request and reused across
    dispatch, permissions, and serializer resolution.
    """

    discriminator: Callable[..., str]
    specs: Mapping[str, ServiceSpec[Any, Any, Any]]
    permission_strategy: PermissionStrategy = "union"
