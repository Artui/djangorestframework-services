"""Shared infrastructure for viewset mixins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class _ActionSpecsMixin:
    """Declares the ``action_specs`` class attribute shared by all viewset mixins.

    All per-action mixins and :class:`ActionSerializerResolver` inherit from
    this so the attribute is defined in exactly one place.
    """

    action_specs: ClassVar[Mapping[str, SelectorSpec | ServiceSpec]] = {}
