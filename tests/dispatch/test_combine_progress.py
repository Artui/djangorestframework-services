"""``combine_progress`` and ``null_progress`` are django-service-specs' own.

Their behaviour is tested there. ``null_progress`` has no test module of its own,
so its identity is held here beside the function built on it.
"""

from __future__ import annotations

from django_service_specs.pool.combine_progress import combine_progress as kernel_combine_progress
from django_service_specs.pool.null_progress import null_progress as kernel_null_progress

import rest_framework_services
import rest_framework_services.dispatch
from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.dispatch.null_progress import null_progress


def test_combine_progress_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert combine_progress is kernel_combine_progress
    assert rest_framework_services.combine_progress is kernel_combine_progress


def test_null_progress_is_the_kernel_s_object_at_every_drfs_path() -> None:
    assert null_progress is kernel_null_progress
    assert rest_framework_services.null_progress is kernel_null_progress
    # The package attribute, not the submodule: ``dispatch/__init__.py`` orders its
    # imports so binding the ``null_progress`` submodule cannot shadow the function.
    assert rest_framework_services.dispatch.null_progress is kernel_null_progress
