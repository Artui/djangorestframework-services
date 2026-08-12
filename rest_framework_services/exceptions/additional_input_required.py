from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.exceptions.service_error import ServiceError


class AdditionalInputRequired(ServiceError):
    """A service cannot proceed without a value it was not given.

    Not "what you sent is wrong" — that is
    :class:`~rest_framework_services.exceptions.service_validation_error.ServiceValidationError`
    — but "I got far enough to discover I need something else". The distinction
    is that this is usually *conditional on what the service found*, so it
    cannot be expressed as a required input on the serializer::

        def delete_rows(*, data, confirmed: bool = False):
            doomed = rows_matching(data)
            if len(doomed) > 100 and not confirmed:
                raise AdditionalInputRequired(
                    f"{len(doomed)} rows match. Confirm to proceed.",
                    schema={"confirmed": {"type": "boolean"}},
                )
            ...

    ``schema`` describes what is missing, keyed by the input name the service
    expects it back under. A transport that can ask renders it; one that cannot
    still has a message worth showing.

    **The answer comes back as ordinary input**, on every transport. An HTTP
    client re-submits with ``confirmed`` in the body; an MCP client is asked and
    its answer is merged into the tool arguments before dispatch. Either way the
    service reads it as a normal parameter, which is why raising is the whole of
    the service's involvement — there is no callback to hold and no session to
    resume.

    **A ``ServiceError`` subclass deliberately.** A transport that has never
    heard of this still does something sensible with it: the operation could not
    be completed, and here is why. Transports that *can* ask catch it before the
    generic handler and do better.

    **What is deliberately not here.** An earlier draft put the whole
    interaction in this package — a callable pool seed, an accept / decline /
    cancel result type, and a contract in which the service is re-executed from
    the top on retry. That was one protocol's interaction model (MCP's
    multi-round-trip requests) wearing a generic name. The tell was that its
    pool seed had to *raise* by default, where every other seed can be a no-op:
    a seed is safe to declare anywhere precisely because a transport that cannot
    honour it may drop it, and that one could not. What survives here is the
    part that is genuinely transport-neutral — a service saying what it needs —
    and each transport owns how it asks.
    """

    def __init__(self, message: str, *, schema: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.schema: Mapping[str, Any] | None = schema


__all__ = ["AdditionalInputRequired"]
