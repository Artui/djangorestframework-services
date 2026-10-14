"""``NotPermitted`` - the kernel's denial, which DRF answers 403."""

from __future__ import annotations

from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from rest_framework.exceptions import PermissionDenied


class NotPermitted(KernelNotPermitted, PermissionDenied):
    """A permission check refused the principal, as both the kernel's ``NotPermitted`` and DRF's ``PermissionDenied``.

    Every published consumer of this package catches DRF's
    ``PermissionDenied`` by name and ends the attempt on it, and DRF's
    exception handler answers it 403. None of them catches the kernel's class,
    so a denial dispatch makes leaves as this, and an ``except`` written
    against either side catches it.

    Deliberately **not** a service error, which the kernel's class is not
    either: an agent reads a service refusal as something to route around and
    keeps trying, where a denial should end the attempt.

    ``message`` is the kernel's, and ``detail`` is the same words as DRF's
    ``ErrorDetail``, coded ``"permission_denied"`` unless ``code`` is given.

    The kernel's base comes first, so its ``__init__`` sets ``message``; with
    DRF's first, nothing would, and ``test_the_default_is_the_kernels_message``
    fails. DRF's ``__init__`` is then run by name rather than left to the
    kernel's ``super()`` chain, which reaches it only because of where the
    bases sit and passes it no ``code``: ``test_a_code_given_is_kept`` is what
    fails without the call.
    """

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        KernelNotPermitted.__init__(self, message)
        PermissionDenied.__init__(self, self.message, code)
