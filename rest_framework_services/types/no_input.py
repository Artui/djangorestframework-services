"""``NoInput`` — sentinel type for strict services that expect no body data."""

from __future__ import annotations


class NoInput:
    """Sentinel type for the ``InputT`` slot when a service expects no body.

    Pair with [`DeleteService`][rest_framework_services.services.delete_service.DeleteService] when the spec has no ``input_serializer``:

        @implements(DeleteService[NoInput, Author, None])
        def delete_author(
            *,
            instance: Author,
            **extras: Any,
        ) -> None: ...

    The class itself is never instantiated — it exists purely to bind the
    ``InputT`` type variable in a way that is searchable in IDEs and docs.
    """
