"""``PrincipalUnavailable`` - the kernel's refusal of a principal who cannot act, which DRF answers 403."""

from __future__ import annotations

from django_service_specs.authorization.principal_unavailable import (
    PrincipalUnavailable as KernelPrincipalUnavailable,
)
from rest_framework.exceptions import PermissionDenied


class PrincipalUnavailable(KernelPrincipalUnavailable, PermissionDenied):
    """The principal cannot act, as both the kernel's ``PrincipalUnavailable`` and DRF's ``PermissionDenied``.

    Raised for an identifier naming nobody or a deactivated account, before
    any permission check. It reaches every published consumer's ladder as the
    denial DRF names, answered 403 and ending the attempt, and an ``except``
    written against the kernel's class catches it too.

    Deliberately **not** a service error, which the kernel's class is not
    either: a refused principal is terminal, never something to route around.

    ``message`` is the kernel's, and ``detail`` is the same words as DRF's
    ``ErrorDetail``, coded ``"permission_denied"`` unless ``code`` is given.
    The default is the kernel's wording, not DRF's, since there is nobody to
    refuse a permission to.

    The kernel's base comes first, so its ``__init__`` sets ``message``; with
    DRF's first, nothing would, and
    ``test_the_default_is_the_kernels_message_not_drfs`` fails. DRF's
    ``__init__`` is then run by name, as ``NotPermitted``'s is, because the
    kernel's ``super()`` chain passes it no ``code``:
    ``test_a_code_given_is_kept`` is what fails without the call.
    """

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        KernelPrincipalUnavailable.__init__(self, message)
        PermissionDenied.__init__(self, self.message, code)
