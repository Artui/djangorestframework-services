"""``NoInput`` — sentinel type for strict services that expect no body data."""

from __future__ import annotations


class NoInput:
    """Sentinel type for the ``InputT`` slot when a service expects no body.

    Pair with :class:`DeleteService` when the spec has no ``input_serializer``::

        @implements(DeleteService[NoInput, Author, None, NoKwargs])
        def delete_author(
            *,
            instance: Author,
            **extras: Unpack[NoKwargs],
        ) -> None: ...

    The class itself is never instantiated — it exists purely to bind the
    ``InputT`` type variable in a way that is searchable in IDEs and docs.
    """
