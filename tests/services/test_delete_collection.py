"""``delete_collection`` and its async twin are django-service-specs' own.

Their behaviour is tested there. ``adelete_collection`` has no test module of its
own, so its identity is held here beside its sync twin.
"""

from __future__ import annotations

from django_service_specs.mutations.adelete_collection import (
    adelete_collection as kernel_adelete_collection,
)
from django_service_specs.mutations.delete_collection import (
    delete_collection as kernel_delete_collection,
)

import rest_framework_services
from rest_framework_services.services.adelete_collection import adelete_collection
from rest_framework_services.services.delete_collection import delete_collection


def test_delete_collection_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert delete_collection is kernel_delete_collection
    assert rest_framework_services.delete_collection is kernel_delete_collection


def test_adelete_collection_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert adelete_collection is kernel_adelete_collection
    assert rest_framework_services.adelete_collection is kernel_adelete_collection
