"""``SerializerValidator`` - the kernel's Validator over a DRF serializer."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet

# The one refusal this module raises. The kernel's class for now; the DRF
# adapter's own, which is a DRF ``ValidationError`` as well, replaces this
# import and nothing else.
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.validation.validated import Validated
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator
from rest_framework import serializers

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.service_specs.utils import (
    bind_serializer,
    describe_serializer,
    input_parameters,
)


class SerializerValidator(Validator):
    """A Validator whose declaration is a DRF serializer, or a dataclass read through one.

    It takes what a drfs spec's ``input_serializer`` takes: a ``Serializer``
    subclass, ``ModelSerializer`` and ``DataclassSerializer`` included, or a
    dataclass, which is validated through a ``DataclassSerializer`` exactly as
    drfs' dispatch validates it. Anything else is refused with
    ``ImproperlyConfigured`` when the Validator is built.

    ``parameters()`` walks the serializer's fields, in order, as the field walk
    in ``service_specs/utils.py`` reads them: a nested serializer is an object
    with Parameters of its own, ``many=True`` and a ``ListField`` an array, a
    file field a ``file``, choices with their labels; a ``HiddenField`` and a
    read-only field are no parameter. It is read on each call rather than
    when the Validator is built, because specs are built at import time, when
    a ``ModelSerializer`` cannot yet read its model, and so that ``help`` is
    rendered in the language active when it is asked for. It never queries.

    ``validate()`` builds the serializer as a DRF view would:

    - **with the target as** ``instance``, so an update's uniqueness checks
      exclude the row being updated. The queryset a collection selector
      resolves is no row, so a call over one binds as a create, as drfs'
      dispatch binds it; the target is handed over as it is, since a
      serializer, unlike a ``ModelForm``, writes nothing onto its instance
      while it validates.
    - **partial as the call is**, the context's ``partial``, so a partial call
      may leave out a required field and returns only what was sent.
    - **under the serializer context DRF builds on HTTP**, ``request``,
      ``format`` and ``view``, around an offline request whose ``user`` is the
      principal and whose ``data`` is the arguments, built by
      [`build_offline_context`][rest_framework_services.dispatch.build_offline_context.build_offline_context].
      A ``CurrentUserDefault`` therefore reads the principal, and a field or a
      ``validate()`` reading ``self.context["request"]`` finds a request
      rather than a ``KeyError``.

    A refusal is one
    [`InvalidArguments`][django_service_specs.parameters.invalid_arguments.InvalidArguments]
    carrying the serializer's ``errors`` unchanged: DRF's ``ErrorDetail``
    leaves, each with its code, cross-field messages under
    ``non_field_errors``, and a nested list's errors in whichever shape the
    installed DRF writes them.

    ``validated()`` hands the service what drfs' dispatch hands it: ``data``
    is the serializer's ``validated_data`` - the validated mapping, or the
    dataclass instance a ``DataclassSerializer`` builds - and the bound
    serializer is bound for the call as ``serializer``. ``values`` are the
    mapping's entries, keyed as DRF keys them, by each field's ``source``; for a
    dataclass, the instance's ``__init__`` fields.

    **A partial call over a dataclass is refused**, with
    ``ImproperlyConfigured``: a ``DataclassSerializer`` builds its instance
    from every field, and one a partial call left out fails as a ``TypeError``
    from inside the library.
    """

    def __init__(self, serializer: type[Any]) -> None:
        # One branch to coverage, so each condition is held by its own test:
        # the ``type`` by test_anything_but_a_serializer_class_or_a_dataclass_is_refused_when_declared[an-instance],
        # which ``issubclass`` would raise on; the serializer class by
        # test_parameters_are_the_walked_fields, and the dataclass by
        # test_a_dataclass_s_data_is_its_instance_and_its_values_its_fields,
        # each of which is refused without its own; and [another-class] holds
        # that the two are the only declarations taken.
        if not (
            isinstance(serializer, type)
            and (
                issubclass(serializer, serializers.Serializer)
                or dataclasses.is_dataclass(serializer)
            )
        ):
            raise ImproperlyConfigured(
                f"SerializerValidator takes a Serializer subclass or a dataclass; got {serializer!r}."
            )
        self.serializer = serializer

    def parameters(self) -> Parameters:
        """The serializer's input fields, as Parameters. Never queries."""
        return input_parameters(describe_serializer(self.serializer))

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        """The validated values, keyed by source, or ``InvalidArguments`` carrying DRF's detail."""
        return self._validated(arguments, context).values

    def validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Validated:
        """The values, ``validated_data`` as the call's ``data``, and the bound ``serializer``."""
        return self._validated(arguments, context)

    def _validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Validated:
        # Neither public method calls the other, so a subclass may extend
        # either through the other without the two calling each other forever.
        is_dataclass = dataclasses.is_dataclass(self.serializer)
        # One branch to coverage: test_a_partial_call_over_a_dataclass_is_refused
        # holds both together, and each alone is held by a call it must not
        # refuse: test_a_partial_call_takes_a_required_field_left_out_and_returns_what_was_sent
        # (a partial serializer) and test_a_dataclass_s_data_is_its_instance_and_its_values_its_fields
        # (a whole dataclass call).
        if context.partial and is_dataclass:
            raise ImproperlyConfigured(
                f"SerializerValidator({self.serializer.__qualname__}) cannot validate a partial "
                "call: a DataclassSerializer builds its instance from every field. Declare the "
                "input as a Serializer subclass to take a partial call."
            )
        serializer = self._bind(arguments, context)
        if not serializer.is_valid():
            raise InvalidArguments(serializer.errors)
        data: Any = serializer.validated_data
        values = (
            {f.name: getattr(data, f.name) for f in dataclasses.fields(data) if f.init}
            if is_dataclass
            else dict(data)
        )
        return Validated(values=values, data=data, pool={"serializer": serializer})

    def _bind(
        self, arguments: Mapping[str, Any], context: ValidationContext
    ) -> serializers.Serializer:
        """The serializer, bound to ``arguments`` for this call."""
        offline = build_offline_context(context.principal, arguments)
        target = context.target
        return bind_serializer(
            self.serializer,
            data=arguments,
            # The collection a collection selector resolves is no row to update.
            instance=None if isinstance(target, QuerySet) else target,
            partial=context.partial,
            context=base_serializer_context(view=offline.view, request=offline.request),
        )
