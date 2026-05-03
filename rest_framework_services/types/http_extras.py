"""``HttpExtras[UserT]`` — generic ``TypedDict`` for HTTP-bound strict services.

Use when a strict service / selector wants the ``request`` and ``user`` keys
the framework injects from a view, without re-declaring them in every
``ExtraT`` ``TypedDict``. Subclass to add per-action extras::

    class CreateAuthorKwargs(HttpExtras[MyUser]):
        tenant_id: int

    @implements(StrictCreateService[AuthorIn, CreateAuthorKwargs, Author])
    def create_author(
        *, data: AuthorIn, **extras: Unpack[CreateAuthorKwargs]
    ) -> Author:
        user = extras["user"]  # typed as MyUser
        ...

Parameterising on ``UserT`` lets each project narrow ``user`` to its own
``AUTH_USER_MODEL`` without the library baking a union in. The default
``UserT = Any`` matches the convention in
:mod:`rest_framework_services.services.utils` — at runtime ``request.user``
may be a concrete user, an ``AnonymousUser``, or ``None`` for requests that
bypassed authentication middleware.
"""

from __future__ import annotations

from typing import Any, Generic

from rest_framework.request import Request
from typing_extensions import TypedDict, TypeVar

UserT = TypeVar("UserT", default=Any)


class HttpExtras(TypedDict, Generic[UserT]):
    request: Request
    user: UserT
