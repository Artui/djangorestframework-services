from __future__ import annotations

from rest_framework_services.exceptions.service_conflict import ServiceConflict


class ActionUnavailable(ServiceConflict):
    """The operation is not possible right now, for a reason with a stable name.

    Raised by the dispatcher when one of a spec's
    [`Affordance`][rest_framework_services.types.affordance.Affordance] conditions is
    not met, carrying that affordance's ``code`` and its ``reason`` as the message.
    Over HTTP it is a ``409`` whose body is ``{"detail": <reason>, "code": <code>}``.

    A [`ServiceConflict`][rest_framework_services.exceptions.service_conflict.ServiceConflict]
    subclass rather than a ``code=`` argument on its parent, for three reasons. The
    ``409`` mapping needs no change, because a subclass is matched by its parent's
    branch. A transport that wants the code matches this class ahead of its
    generic conflict handler, and one that has never heard of it still reports a
    conflict. And a ``ServiceConflict`` raised by hand from a precondition genuinely
    has no code and never will, so a nullable ``code`` on the parent would be a
    field that lies at every other call site.

    ``code`` names the rule and is safe for every audience; the message is written
    for an operator and may describe internal state, so a transport serving an
    agent should read ``code`` and leave the message out.

    Like every member, it must be matched **before** a generic ``ServiceConflict``
    or ``ServiceError`` handler, or the subclass check swallows it.
    """

    default_message: str = "This action is not available right now."

    def __init__(self, message: str | None = None, *, code: str) -> None:
        super().__init__(message)
        self.code: str = code


__all__ = ["ActionUnavailable"]
