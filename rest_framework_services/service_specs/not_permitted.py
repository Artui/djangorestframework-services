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

    Both ``__init__``s run by name, so neither depends on the order of the
    bases, which is a choice (the kernel's first, as the class is named) and
    no test can tell from the other. The kernel's sets ``message``:
    ``test_the_default_is_the_kernels_message`` fails without it. DRF's sets
    ``detail`` with the ``code`` given, which the kernel's ``super()`` chain
    never passes: ``test_a_code_given_is_kept`` fails without it.
    """

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        KernelNotPermitted.__init__(self, message)
        PermissionDenied.__init__(self, self.message, code)
