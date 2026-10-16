"""``implements`` is django-service-specs' own; its behaviour is tested there.

Static drift detection against drfs' ``HttpExtras`` is still exercised by ``ty``
against ``tests/services/strict_drift_fixtures.py``.
"""

from __future__ import annotations

from django_service_specs.services.implements import implements as kernel_implements

import rest_framework_services
from rest_framework_services.implements import implements


def test_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert implements is kernel_implements
    assert rest_framework_services.implements is kernel_implements
