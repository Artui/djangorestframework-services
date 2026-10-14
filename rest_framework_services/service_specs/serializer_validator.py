"""``SerializerValidator`` - the kernel's Validator over a DRF serializer."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.pool.reserved_pool_seeds import RESERVED_POOL_SEEDS
from django_service_specs.validation.validated import Validated
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.utils import (
    bind_serializer,
    describe_serializer,
    input_parameters,
    input_view_layers,
    serializer_context,
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
      ``format`` and ``view``. The ``request`` and ``view`` the call
      registered as pool seeds are the serializer's own, read from
      ``ValidationContext.ambient`` as the kernel resolved them. A call that
      registered neither gets an offline request whose ``user`` is the
      principal and whose ``data`` is the arguments, built by
      [`build_offline_context`][rest_framework_services.service_specs.build_offline_context.build_offline_context],
      so a ``CurrentUserDefault`` reads the principal and a field or a
      ``validate()`` reading ``self.context["request"]`` finds a request
      rather than a ``KeyError``. What a registered request's ``user`` is, is
      the transport's to make the principal; the Validator never writes to a
      call's own objects. Over that, the input half of the view hooks a DRF
      edge carries for the call (``input_view_layers``), then ``serializer_context``, the spec's own
      ``input_serializer_context``, each winning on a key it shares with what
      it layers over (see
      ``serializer_context`` in ``service_specs/utils.py``).
      The callable is asked once per call, a ``many`` call's included, and
      declares what it reads from the call's pool: ``request``, ``view``,
      ``user``, a registered seed, or an argument.

    A refusal is one
    [`InvalidArguments`][rest_framework_services.service_specs.invalid_arguments.InvalidArguments],
    the kernel's refusal that is DRF's ``ValidationError`` as well, carrying
    the serializer's ``errors`` unchanged: DRF's ``ErrorDetail`` leaves, each
    with its code, cross-field messages under ``non_field_errors``, and a
    nested list's errors in whichever shape the installed DRF writes them.

    ``validated()`` hands the service what drfs' dispatch hands it: ``data``
    is the serializer's ``validated_data`` - the validated mapping, or the
    dataclass instance a ``DataclassSerializer`` builds - and the bound
    serializer is bound for the call as ``serializer``. ``values`` follow
    what ``validated_data`` is, as drfs' dispatch reads it: a mapping's
    entries, keyed as DRF keys them, by each field's ``source``; a dataclass
    instance's ``__init__`` fields, whether a dataclass was declared or a
    ``DataclassSerializer`` subclass was; and nothing for any other object a
    serializer's ``validate()`` returns, which reaches the service only as
    ``data``. **A key named like a seed of the call is a value only in**
    ``data``: DRF's ``HiddenField(default=CurrentUserDefault())`` named
    ``user``, or a key a ``validate()`` adds named ``result``, stays in
    ``validated_data`` as the serializer built it, and the service's ``user``
    is the call's principal, as drfs always handed them (see ``_unseeded``).

    **A partial call over a dataclass is refused**, a ``DataclassSerializer``
    subclass included: one builds its instance from every field, and a field
    a partial call left out fails as a ``TypeError`` from inside the library.
    ``supports_partial`` is therefore answered per instance, ``False`` for
    either declaration and ``True`` for any other serializer, so a spec
    declaring ``partial=True`` over a dataclass is refused when it is built.
    A partial call made on the Validator directly is refused with
    ``ImproperlyConfigured`` as well.

    ``validated_many()`` validates a ``many`` spec's rows as DRF's own list
    serializer does, once: the serializer's child class under
    ``many=True`` (so a ``Meta.list_serializer_class`` and its list-level
    ``validate()`` run), with the call's context and partial flag. The one
    list serializer is bound for the call as ``serializer``, ``data`` is the
    list of each row's ``validated_data`` and the values are the rows' values
    under ``many_argument``. A refusal is an ``InvalidArguments`` whose detail
    keys each failing row by the index the caller's list gave it, whichever of
    DRF's two shapes the installed version reports item errors in, and whose
    list-level message sits under ``non_field_errors``.
    """

    def __init__(
        self,
        serializer: type[Any],
        *,
        serializer_context: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
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
        self.serializer_context = serializer_context
        # The kernel reads this per instance: the answer belongs to the
        # declaration. A dataclass read through a DataclassSerializer, and a
        # DataclassSerializer subclass declared outright (as the gallery
        # declares its inputs), each build the instance from every field. Each
        # is held by its own case of test_supports_partial_is_answered_per_declaration.
        self.supports_partial = not (
            dataclasses.is_dataclass(serializer) or issubclass(serializer, DataclassSerializer)
        )

    def parameters(self) -> Parameters:
        """The serializer's input fields, as Parameters. Never queries."""
        return input_parameters(describe_serializer(self.serializer))

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        """The validated values, keyed by source, or ``InvalidArguments`` carrying DRF's detail."""
        return self._validated(arguments, context).values

    def validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Validated:
        """The values, ``validated_data`` as the call's ``data``, and the bound ``serializer``."""
        return self._validated(arguments, context)

    def validated_many(
        self,
        rows: Mapping[int, Mapping[str, Any]],
        context: ValidationContext,
        *,
        many_argument: str,
    ) -> Validated:
        """The rows validated once as DRF's list serializer, the one serializer bound for the call."""
        self._refuse_partial(context)
        arguments = list(rows.values())
        serializer = bind_serializer(
            self.serializer,
            data=arguments,
            many=True,
            partial=context.partial,
            context=self._context(context, arguments),
        )
        if not serializer.is_valid():
            raise InvalidArguments(_by_row(serializer.errors, list(rows)))
        data: list[Any] = list(serializer.validated_data)
        return Validated(
            values={many_argument: [_values(row) for row in data]},
            data=data,
            pool={"serializer": serializer},
        )

    def _validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Validated:
        # Neither public method calls the other, so a subclass may extend
        # either through the other without the two calling each other forever.
        self._refuse_partial(context)
        serializer = self._bind(arguments, context)
        if not serializer.is_valid():
            raise InvalidArguments(serializer.errors)
        data: Any = serializer.validated_data
        return Validated(
            values=_unseeded(_values(data), context), data=data, pool={"serializer": serializer}
        )

    def _refuse_partial(self, context: ValidationContext) -> None:
        # One branch to coverage: test_a_partial_call_over_a_dataclass_is_refused
        # holds both together, and each alone is held by a call it must not
        # refuse: test_a_partial_call_takes_a_required_field_left_out_and_returns_what_was_sent
        # (a partial serializer) and test_a_dataclass_s_data_is_its_instance_and_its_values_its_fields
        # (a whole dataclass call). The kernel refuses a partial spec over
        # this when it is built; this is the Validator called directly.
        if context.partial and not self.supports_partial:
            raise ImproperlyConfigured(
                f"SerializerValidator({self.serializer.__qualname__}) cannot validate a partial "
                "call: a DataclassSerializer builds its instance from every field. Declare the "
                "input as a Serializer subclass to take a partial call."
            )

    def _bind(
        self, arguments: Mapping[str, Any], context: ValidationContext
    ) -> serializers.Serializer:
        """The serializer, bound to ``arguments`` for this call."""
        target = context.target
        return bind_serializer(
            self.serializer,
            data=arguments,
            # The collection a collection selector resolves is no row to update.
            instance=None if isinstance(target, QuerySet) else target,
            partial=context.partial,
            context=self._context(context, arguments),
        )

    def _context(
        self, context: ValidationContext, arguments: Mapping[str, Any] | list[Any]
    ) -> dict[str, Any]:
        """The serializer context for this call: DRF's, the view's input hooks, then the spec's."""
        return serializer_context(
            context.principal,
            context.ambient,
            arguments,
            view_layers=input_view_layers(),
            provider=self.serializer_context,
        )


def _by_row(errors: Any, indices: list[int]) -> dict[Any, Any]:
    """A list serializer's errors, each failing row keyed by the caller's own index.

    DRF counts positions in the list it was handed, and the shape check may
    have refused rows before it (test_validated_many_keys_a_row_s_refusal_by_the_caller_s_index_not_the_position,
    which names row 2 where DRF says 1). DRF below 3.18 reports item errors as
    a list with an empty entry for every valid row, and from 3.18 as a mapping
    of the invalid rows' positions; the list becomes the mapping, as the kernel
    reads it
    (test_item_errors_reported_as_a_list_are_keyed_by_the_callers_index_too).
    The list's own refusals are a mapping under ``non_field_errors`` on every
    version and keep that key (test_a_list_level_refusal_sits_under_non_field_errors).
    """
    if isinstance(errors, list):
        errors = {position: row for position, row in enumerate(errors) if row}
    return {indices[key] if isinstance(key, int) else key: detail for key, detail in errors.items()}


def _unseeded(values: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
    """``values`` less every key a seed of the call, or the bound serializer, holds.

    The kernel seeds those names itself and refuses a validated value named
    like one, where a serializer has always been free to validate one into
    ``validated_data``: the service read it in ``data`` and the seed under its
    name. So such a key is handed on in ``data`` alone, and the seed is never
    replaced. Each part of the set is held by the test that fails without it:
    ``RESERVED_POOL_SEEDS`` by
    test_a_key_a_validate_adds_named_like_a_seed_stays_in_data (``result`` is
    no seed of the call's base pool), the call's own seeds by
    test_a_key_named_like_a_registered_seed_stays_in_data, and ``serializer``,
    which this Validator binds for the call, by
    test_a_key_a_validate_adds_named_like_the_bound_serializer_stays_in_data.
    """
    seeded = RESERVED_POOL_SEEDS | context.ambient.keys() | {"serializer"}
    return {name: value for name, value in values.items() if name not in seeded}


def _values(data: Any) -> dict[str, Any]:
    """The values a call's ``validated_data`` spreads by name, as drfs' dispatch spreads them.

    Read off the value rather than the declaration, so a declared
    ``DataclassSerializer`` subclass is read as the dataclass it builds
    (test_a_declared_dataclass_serializer_hands_its_instance_and_its_fields),
    and a ``validate()`` returning some other object spreads nothing
    (test_an_object_a_validate_returns_is_data_and_spreads_nothing).
    """
    if isinstance(data, Mapping):
        return dict(data)
    if dataclasses.is_dataclass(data):
        return {f.name: getattr(data, f.name) for f in dataclasses.fields(data) if f.init}
    return {}
