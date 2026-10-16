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
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] under a
    string key. A ``discriminator`` callable inspects the request and returns the key;
    dispatch then proceeds through the chosen spec exactly as a plain ``ServiceSpec``
    would. Usable where a view takes a ``ServiceSpec``: an ``action_specs`` entry
    and the ``spec=`` of ``@service_action``. A ``SpecRegistry`` and the other
    kernel entry points refuse it with a pointer at its variants, which are
    registered one per name.

    ```python
    PolymorphicServiceSpec(
        discriminator=resolve_flow,        # pool: {request, data, user, view} → key
        specs={"email": email_spec, "token": token_spec},
    )
    ```

    The discriminator is resolved once per request and reused across dispatch,
    permissions, and serializer resolution, and the resolved concrete spec flows
    through the shared action→spec chain — so the chosen variant's serializer
    context, kwargs, and output pipeline all apply.

    **There is no ``metadata`` field here.** The wrapper is never dispatched, so
    ``metadata`` belongs on the variants; a permission class that reads it off
    the spec it finds on the view therefore finds *this* wrapper, which has none,
    and under the default ``permission_strategy="union"`` there is no variant yet
    to fall back to — permissions run before discrimination. Express a
    metadata-driven rule per variant, via each variant's own
    ``permission_classes``, rather than reading the wrapper.

    Attributes:
        discriminator: Resolved through the framework keyword pool — it declares
            any subset of ``request`` / ``data`` (the *raw* ``request.data``) /
            ``user`` / ``view`` (or ``**kwargs``) — and returns a key present in
            ``specs``. For a no-match / rejected payload it should **raise**
            (e.g. [`ServiceValidationError`][rest_framework_services.exceptions.service_validation_error.ServiceValidationError],
            which the view layer maps to a 400); returning a key absent from
            ``specs`` is a configuration error and raises
            ``ImproperlyConfigured``.
        specs: The variants, keyed by the value ``discriminator`` returns.
        permission_strategy: How ``get_permissions`` behaves, given that DRF runs
            permissions before the body is necessarily parsed. ``"union"`` (the
            default) requires the union of every variant's ``permission_classes``
            — no early body read, and the conservative/secure failure mode, since
            a mis-declared discriminator branch can't *widen* access; for the
            common "same auth, different payload shapes" case it is identical to
            ``"require_identical"``. ``"discriminate"`` runs the discriminator
            early (reading the raw body) and applies only the chosen variant's
            ``permission_classes`` — most precise, for the genuine "each variant
            is a different privilege" case. ``"require_identical"`` is validated
            at ``as_view()`` time to require every variant to declare the same
            ``permission_classes``, sidestepping the ordering entirely.
    """

    discriminator: Callable[..., str]
    specs: Mapping[str, ServiceSpec[Any, Any, Any]]
    permission_strategy: PermissionStrategy = "union"

    def as_kernel_spec(self) -> None:
        """Refuse compiling to one spec, naming the remedy.

        Every kernel entry point (``SpecRegistry.register``, ``kernel_spec``,
        ``dispatch``) asks a declaration for ``as_kernel_spec()``. A wrapper picks
        its variant per request, so there is no one spec to answer with, and
        without this method the kernel's refusal would be a bare type error that
        says nothing of the variants. The registry prefixes the name it was
        registering (test_a_polymorphic_spec_is_refused_with_a_pointer_at_its_variants).

        Annotated ``None`` on purpose, never ``NoReturn``: the bottom type would
        make this wrapper a structural ``SpecSource``, so a type checker would
        accept registering it, where it refuses it today.

        Raises:
            TypeError: always.
        """
        raise TypeError(
            "a PolymorphicServiceSpec cannot be registered or dispatched as one spec. "
            "Register each variant from its `specs` mapping under its own name - "
            "transports project one operation per variant, not a union."
        )
