"""``arun_service`` is django-service-specs' own; its behaviour is tested there."""

from __future__ import annotations

from django_service_specs.services.arun_service import arun_service as kernel_arun_service

import rest_framework_services
from rest_framework_services.services.arun_service import arun_service


def test_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert arun_service is kernel_arun_service
    assert rest_framework_services.arun_service is kernel_arun_service
