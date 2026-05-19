"""Service Protocols — typed shapes for mutation callables.

Importing the Protocols is purely opt-in: ``ServiceSpec.service`` accepts any
plain callable. Annotate your service against the matching Protocol when you
want IDE / type-checker support for the keys the framework provides.

Each Protocol takes a trailing ``ExtraT`` TypeVar with a private default that
accepts arbitrary :class:`~typing.Any`-typed keyword arguments (the "lenient"
shape). Bind ``ExtraT`` to a concrete ``TypedDict`` to switch to the "strict"
shape — type checkers will then enforce that the service declares exactly
those extras (no more, no less).

The ``create_model`` / ``update_model`` / ``delete_model`` factories (plus
their ``acreate_model`` / ``aupdate_model`` / ``adelete_model`` async
siblings) return ready-made service callables for the common case where the
entire body is a one-line wrapper over the mutation helpers.
"""

from rest_framework_services.services.acall_service import acall_service
from rest_framework_services.services.acreate_model import acreate_model
from rest_framework_services.services.adelete_model import adelete_model
from rest_framework_services.services.aupdate_model import aupdate_model
from rest_framework_services.services.call_service import call_service
from rest_framework_services.services.create_model import create_model
from rest_framework_services.services.create_service import CreateService
from rest_framework_services.services.delete_model import delete_model
from rest_framework_services.services.delete_service import DeleteService
from rest_framework_services.services.update_model import update_model
from rest_framework_services.services.update_service import UpdateService

__all__ = [
    "CreateService",
    "DeleteService",
    "UpdateService",
    "acall_service",
    "acreate_model",
    "adelete_model",
    "aupdate_model",
    "call_service",
    "create_model",
    "delete_model",
    "update_model",
]
