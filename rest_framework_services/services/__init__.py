"""Service Protocols — typed shapes for mutation callables.

Importing the Protocols is purely opt-in: ``ServiceSpec.service`` accepts any
plain callable. Annotate your service against the matching Protocol when you
want IDE / type-checker support for the keys the framework provides.

Each Protocol types ``**extras`` as :class:`~typing.Any`, so any service
function that declares ``**extras: Any`` (or omits the keyword-spread
entirely and reads only declared keys) conforms. To get IDE / type-checker
help on extras keys, annotate your function with
``**extras: Unpack[YourTypedDict]`` — keys must be ``NotRequired`` to keep
the function Protocol-conformant. The Protocols themselves do not carry an
extras-shape parameter; see ``CreateService`` for the rationale.

The ``create_model`` / ``update_model`` / ``delete_model`` factories (plus
their ``acreate_model`` / ``aupdate_model`` / ``adelete_model`` async
siblings) return ready-made service callables for the common case where the
entire body is a one-line wrapper over the mutation helpers.
"""

from rest_framework_services.services.acall_service import acall_service
from rest_framework_services.services.acreate_model import acreate_model
from rest_framework_services.services.adelete_collection import adelete_collection
from rest_framework_services.services.adelete_model import adelete_model
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.aupdate_model import aupdate_model
from rest_framework_services.services.call_service import call_service
from rest_framework_services.services.create_model import create_model
from rest_framework_services.services.create_service import CreateService
from rest_framework_services.services.delete_collection import delete_collection
from rest_framework_services.services.delete_model import delete_model
from rest_framework_services.services.delete_service import DeleteService
from rest_framework_services.services.run_service import run_service
from rest_framework_services.services.update_model import update_model
from rest_framework_services.services.update_service import UpdateService

__all__ = [
    "CreateService",
    "DeleteService",
    "UpdateService",
    "acall_service",
    "acreate_model",
    "adelete_collection",
    "adelete_model",
    "arun_service",
    "aupdate_model",
    "call_service",
    "create_model",
    "delete_collection",
    "delete_model",
    "run_service",
    "update_model",
]
