"""``Selector`` and ``AsyncSelector`` are django-service-specs' own Protocols."""

from __future__ import annotations

from django_service_specs.selectors.async_selector import AsyncSelector as KernelAsyncSelector
from django_service_specs.selectors.selector import Selector as KernelSelector

import rest_framework_services
from rest_framework_services.selectors.async_selector import AsyncSelector
from rest_framework_services.selectors.selector import Selector


def test_selector_is_the_kernel_s_protocol() -> None:
    assert Selector is KernelSelector
    assert rest_framework_services.Selector is KernelSelector


def test_async_selector_is_the_kernel_s_protocol() -> None:
    assert AsyncSelector is KernelAsyncSelector
    assert rest_framework_services.AsyncSelector is KernelAsyncSelector
