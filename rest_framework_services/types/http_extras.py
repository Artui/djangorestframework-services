"""``HttpExtras[UserT]`` — generic ``TypedDict`` for HTTP-bound services.

Use when a service / selector wants the ``request`` and ``user`` keys
the framework injects from a view, without re-declaring them in every
extras ``TypedDict``. Subclass with ``total=False`` (or annotate each new
field as ``NotRequired``) to add per-action extras:

    class CreateAuthorKwargs(HttpExtras[MyUser], total=False):
        tenant_id: int

    @implements(CreateService[AuthorIn, Author])
    def create_author(
        *, data: AuthorIn, **extras: Unpack[CreateAuthorKwargs]
    ) -> Author:
        user = extras.get("user")  # typed as MyUser | None
        ...

``HttpExtras`` itself is declared ``total=False`` — every field is optional.
The framework's kwargs pool changes shape per view/action, so the service
may be called without any specific key. Subclasses should also use
``total=False`` (or per-field ``NotRequired``) so the resulting function
stays Protocol-conformant: under PEP 692, ``Unpack[<TypedDict>]`` with any
required key makes the function reject callers that omit that key, which
breaks assignability to ``CreateService`` / ``RetrieveSelector`` / etc.

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

from typing import Any, Generic

from rest_framework.request import Request
from typing_extensions import TypedDict, TypeVar

UserT = TypeVar("UserT", default=Any)


class HttpExtras(TypedDict, Generic[UserT], total=False):
    request: Request
    user: UserT
