"""``HttpExtras[UserT]`` — generic ``TypedDict`` for HTTP-bound strict services.

Use when a strict service / selector wants the ``request`` and ``user`` keys
the framework injects from a view, without re-declaring them in every
``ExtraT`` ``TypedDict``. Subclass to add per-action extras::

    class CreateAuthorKwargs(HttpExtras[MyUser]):
        tenant_id: int

    @implements(CreateService[AuthorIn, Author, CreateAuthorKwargs])
    def create_author(
        *, data: AuthorIn, **extras: Unpack[CreateAuthorKwargs]
    ) -> Author:
        user = extras["user"]  # typed as MyUser
        ...

Parameterising on ``UserT`` lets each project narrow ``user`` to its own
``AUTH_USER_MODEL`` without the library baking a union in. The default
``UserT = Any`` reflects the runtime shape: ``request.user`` may be a
concrete user, an ``AnonymousUser``, or ``None`` for requests that bypassed
authentication middleware. Annotated as ``Any`` so this module stays
import-safe during Django app population (``rest_framework_services`` ships
in ``INSTALLED_APPS`` and is imported during ``apps.populate()``, before
``django.contrib.auth.models`` is loaded).
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from rest_framework.request import Request
from typing_extensions import TypedDict

UserT = TypeVar("UserT", default=Any)


class HttpExtras(TypedDict, Generic[UserT]):
    request: Request
    user: UserT
