"""Service Protocols — typed shapes for mutation callables.

Importing the Protocols is purely opt-in: ``ServiceSpec.service`` accepts any
plain callable. Annotate your service against the matching Protocol when you
want IDE / type-checker support for the keys the framework provides.

For full enforcement (no ``**kwargs: Any`` escape hatch) parameterize against
the ``Strict*`` variants. They use :pep:`692` ``Unpack[TypedDict]`` to pin the
extra-kwargs contract delivered by ``ServiceSpec.kwargs``.
"""

from rest_framework_services.services.acall_service import acall_service
from rest_framework_services.services.call_service import call_service
from rest_framework_services.services.create_service import CreateService
from rest_framework_services.services.delete_service import DeleteService
from rest_framework_services.services.strict_create_service import StrictCreateService
from rest_framework_services.services.strict_delete_service import StrictDeleteService
from rest_framework_services.services.strict_update_service import StrictUpdateService
from rest_framework_services.services.update_service import UpdateService

__all__ = [
    "CreateService",
    "DeleteService",
    "StrictCreateService",
    "StrictDeleteService",
    "StrictUpdateService",
    "UpdateService",
    "acall_service",
    "call_service",
]
