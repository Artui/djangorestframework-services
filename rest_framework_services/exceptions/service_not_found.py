from __future__ import annotations

from rest_framework_services.exceptions.service_error import ServiceError


class ServiceNotFound(ServiceError):
    """The operation's target does not exist, or is not this caller's to see.

    The distinction from a plain
    [`ServiceError`][rest_framework_services.exceptions.service_error.ServiceError]
    is *which* thing is wrong: the resource is absent rather than in the wrong
    state, so a client should stop asking rather than try again differently. Over
    HTTP that is a ``404``; a plain ``ServiceError`` stays a ``422``.

        def move_event(*, user, data):
            event = Event.objects.filter(owner=user, pk=data.event_id).first()
            if event is None:
                raise ServiceNotFound(f"No event {data.event_id}.")

    **Say the same thing for "absent" and "not yours."** Answering ``403`` to a row
    the caller cannot see confirms that it exists, which is why the owner-scoped
    lookup above raises this either way.

    Off HTTP there is no status code to reach for, which is the point of the type:
    a transport that has never heard of it still handles a ``ServiceError``, and one
    that wants to do better matches on the class. It must match **before** its
    generic ``ServiceError`` handler, or the subclass check swallows it.
    """

    default_message: str = "Not found."
