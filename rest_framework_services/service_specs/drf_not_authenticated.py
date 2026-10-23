"""``DRFNotAuthenticated`` - DRF's ``NotAuthenticated`` as the kernel's denial, which DRF answers 401."""

from __future__ import annotations

from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from rest_framework.exceptions import NotAuthenticated


class DRFNotAuthenticated(KernelNotPermitted, NotAuthenticated):
    """No principal was authenticated, as both the kernel's ``NotPermitted`` and DRF's ``NotAuthenticated``.

    What an author callable's or a permission class's DRF ``NotAuthenticated``
    leaves the kernel route as, through
    [`translate_drf_refusals`][rest_framework_services.service_specs.translate_drf_refusals.translate_drf_refusals].
    A transport reading the kernel's classes ends the attempt on it as a
    denial, and DRF's exception handler still answers it 401, with the
    authenticator's challenge, rather than the 403 the dual
    [`NotPermitted`][rest_framework_services.service_specs.not_permitted.NotPermitted]
    would be.

    ``message`` is the kernel's, and ``detail`` is the same words as DRF's
    ``ErrorDetail``, coded ``"not_authenticated"`` unless ``code`` is given.
    The default is DRF's wording, not the kernel's: the refusal is that
    nobody signed in, which the kernel's generic denial does not say
    (test_the_default_is_drf_s_message).

    Both ``__init__``s run by name, as ``NotPermitted``'s do. The kernel's
    sets ``message``: test_a_message_given_is_the_kernel_s fails without it.
    DRF's sets ``detail`` with the ``code`` given, which the kernel's
    ``super()`` chain never passes: test_a_code_given_is_kept fails without it.
    """

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        KernelNotPermitted.__init__(
            self, message if message is not None else str(NotAuthenticated.default_detail)
        )
        NotAuthenticated.__init__(self, self.message, code)
