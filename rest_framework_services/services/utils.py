"""Shared internal helpers for the ``services`` package."""

from __future__ import annotations

from typing import Any, TypeAlias

# ``Any`` is used here intentionally rather than the precise
# ``AbstractBaseUser | AnonymousUser | None`` union: ``rest_framework_services``
# ships in ``INSTALLED_APPS``, and the package is imported during Django's
# ``apps.populate()`` pass — eagerly importing ``django.contrib.auth.models``
# at that point raises ``AppRegistryNotReady``. The lenient Protocols don't
# need a precise user type to be useful; users who want a tighter shape can
# parameterize their own service against the strict variants and annotate
# ``user`` with the concrete class themselves.
UserT: TypeAlias = Any
"""Type of ``request.user`` as it reaches a service or selector.

At runtime ``request.user`` is an ``AbstractBaseUser`` subclass for
authenticated requests, an ``AnonymousUser`` for anonymous ones, or
``None`` if the request bypassed authentication middleware. Annotated as
``Any`` so this module stays import-safe during Django app population.
"""
