"""``paginate_output`` is django-service-specs' own; its behaviour is tested there."""

from __future__ import annotations

from django_service_specs.dispatch.paginate_output import (
    DEFAULT_PAGE_SIZE as KERNEL_DEFAULT_PAGE_SIZE,
)
from django_service_specs.dispatch.paginate_output import (
    paginate_output as kernel_paginate_output,
)

import rest_framework_services
from rest_framework_services.dispatch.paginate_output import DEFAULT_PAGE_SIZE, paginate_output


def test_is_the_kernel_s_object_at_drfs_path_and_root() -> None:
    assert paginate_output is kernel_paginate_output
    assert rest_framework_services.paginate_output is kernel_paginate_output


def test_the_default_page_size_is_the_kernel_s() -> None:
    # Compared by value as well as identity: a small int is one object either way,
    # so identity alone would hold for two separately-declared constants.
    assert DEFAULT_PAGE_SIZE == KERNEL_DEFAULT_PAGE_SIZE == 100
    assert rest_framework_services.DEFAULT_PAGE_SIZE == KERNEL_DEFAULT_PAGE_SIZE
