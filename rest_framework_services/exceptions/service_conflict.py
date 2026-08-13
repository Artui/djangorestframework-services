from __future__ import annotations

from rest_framework_services.exceptions.service_error import ServiceError


class ServiceConflict(ServiceError):
    """The operation collides with the resource's current state.

    A slot already taken, a row someone else moved first, a name already used. The
    resource is there and the request is well-formed; the two are simply
    incompatible right now, and a caller can often resolve it by re-reading and
    trying again. Over HTTP that is a ``409``; a plain
    [`ServiceError`][rest_framework_services.exceptions.service_error.ServiceError]
    stays a ``422``, which says "understood, and still not doing it".

        def slot_is_free(*, user, data):
            if Event.objects.filter(owner=user, day=data.day, hour=data.hour).exists():
                raise ServiceConflict(f"{data.day} at {data.hour}:00 is taken.")

    Reaching for this from a ``preconditions`` predicate is the common case, since
    a state rule is usually exactly this kind of collision.

    Off HTTP there is no status code to reach for, which is the point of the type:
    a transport that has never heard of it still handles a ``ServiceError``, and one
    that wants to do better matches on the class. It must match **before** its
    generic ``ServiceError`` handler, or the subclass check swallows it.
    """

    default_message: str = "Conflict."
