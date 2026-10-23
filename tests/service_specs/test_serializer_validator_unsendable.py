"""``SerializerValidator.unsendable``: the required fields no call can send.

A field named in ``RESERVED_POOL_SEEDS``, in ``BOUND`` or in ``excluded`` is no
parameter, so a call on the kernel route has nowhere to put its value, while
HTTP and ``dispatch_spec`` read it from the body. A transport reads the
attribute with ``getattr(compiled.validator, "unsendable", frozenset())`` to
refuse a spec whose call could never validate. Each test reads it through the
public instance, off a compiled spec where the plan names one.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.types.unknown_arguments import UnknownArguments
from rest_framework import serializers

from rest_framework_services import ServiceSpec
from rest_framework_services.service_specs.serializer_validator import SerializerValidator


class _RequiredUserIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())


class _RequiredSerializerIn(serializers.Serializer):
    title = serializers.CharField()
    serializer = serializers.CharField()


class _OptionalSeedNamesIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.CharField(required=False)
    result = serializers.CharField(default="x")
    serializer = serializers.CharField(required=False, allow_null=True)


class _ReadOnlySeedNamesIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.CharField(read_only=True)
    serializer = serializers.CharField(read_only=True)


class _RequiredResultIn(serializers.Serializer):
    title = serializers.CharField()
    result = serializers.CharField()


class _HiddenUserIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())


class _TenantIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()


def _noop(**_: Any) -> None:
    return None


def _validator_of(serializer: type[Any], *, many: bool = False) -> SerializerValidator:
    spec = ServiceSpec(service=_noop, input_serializer=serializer, many=many, atomic=False)
    validator = kernel_spec(spec).validator
    assert isinstance(validator, SerializerValidator)
    return validator


@pytest.mark.django_db
def test_a_required_field_named_user_is_unsendable() -> None:
    validator = _validator_of(_RequiredUserIn)

    assert validator.parameters().names() == frozenset({"title"})
    assert validator.unsendable == frozenset({"user"})


def test_a_required_field_named_serializer_is_unsendable() -> None:
    """``serializer`` is in ``BOUND``, and HTTP serves it, so it is named like ``user``."""
    validator = _validator_of(_RequiredSerializerIn)

    assert validator.parameters().names() == frozenset({"title"})
    assert validator.unsendable == frozenset({"serializer"})


def test_an_optional_field_is_not_unsendable() -> None:
    """A call that leaves ``user``, ``result`` or ``serializer`` out validates, so none is named."""
    assert _validator_of(_OptionalSeedNamesIn).unsendable == frozenset()


def test_a_read_only_field_is_not_unsendable() -> None:
    assert _validator_of(_ReadOnlySeedNamesIn).unsendable == frozenset()


def test_a_field_named_like_a_registered_seed_is_not_unsendable() -> None:
    """The kernel route declares it a parameter, which a seed or a call then fills.

    The compiled spec does not exclude ``tenant`` (only the DRF edge's variant
    does), so the Validator declares it and a call can send it.
    """
    validator = _validator_of(_TenantIn)

    assert validator.parameters().names() == frozenset({"title", "tenant"})
    assert validator.unsendable == frozenset()


def test_a_field_the_validator_excludes_is_unsendable_when_required() -> None:
    """The name ``excluded`` takes out of ``parameters()`` is the one ``unsendable`` names.

    Held against the case above: only the omission differs, so the attribute
    follows the parameters rather than a list of names of its own.
    """
    validator = SerializerValidator(_TenantIn, excluded=frozenset({"tenant"}))

    assert validator.parameters().names() == frozenset({"title"})
    assert validator.unsendable == frozenset({"tenant"})


def test_a_required_field_named_result_is_unsendable() -> None:
    """``result`` is a dispatcher seed name (``RESERVED_POOL_SEEDS``), like ``user``."""
    assert _validator_of(_RequiredResultIn).unsendable == frozenset({"result"})


def test_a_hidden_field_is_not_unsendable() -> None:
    """A ``HiddenField`` is no input at all, so a call is never asked for it."""
    assert _validator_of(_HiddenUserIn).unsendable == frozenset()


@pytest.mark.django_db
def test_a_partial_spec_names_the_same_fields() -> None:
    """Whether a call may leave the field out is the compiled spec's ``partial``, not this attribute's."""
    spec = ServiceSpec(service=_noop, input_serializer=_RequiredUserIn, partial=True, atomic=False)
    validator = kernel_spec(spec).validator

    assert validator.unsendable == frozenset({"user"})


@pytest.mark.django_db
def test_a_many_specs_row_fields_are_read_through_the_row_serializer() -> None:
    """The Validator of a ``many`` spec holds the row serializer, so a row is what is read.

    A row's required ``user`` is named because the kernel route cannot send it
    either: ``parameters()`` omits it from the row's own Parameters, and a call
    over ``items`` is refused for it under ``REJECT`` (an unknown argument) and
    under ``IGNORE`` (required). A row field named like a registered seed is no
    different from the non-``many`` case: declared, so not named.
    """
    spec = ServiceSpec(service=_noop, input_serializer=_RequiredUserIn, many=True, atomic=False)
    compiled = kernel_spec(spec)
    principal = AnonymousUser()

    for policy in (UnknownArguments.REJECT, UnknownArguments.IGNORE):
        with pytest.raises(KernelInvalidArguments) as excinfo:
            kernel_dispatch(
                compiled,
                principal=principal,
                arguments={"items": [{"title": "t", "user": 1}]},
                grant=Grant(compiled, principal),
                unknown_arguments=policy,
            )
        assert set(excinfo.value.detail["items"][0]) == {"user"}, policy
    assert compiled.validator.unsendable == frozenset({"user"})
    assert _validator_of(_TenantIn, many=True).unsendable == frozenset()


@pytest.mark.django_db
def test_a_lenient_validator_names_the_same_fields() -> None:
    """``lenient`` relaxes what is declared, not which names are declared at all."""
    validator = SerializerValidator(_RequiredUserIn, lenient=True)

    assert validator.parameters().names() == frozenset({"title"})
    assert validator.unsendable == frozenset({"user"})


@pytest.mark.django_db
def test_a_transport_reads_it_with_getattr_off_any_validator() -> None:
    assert getattr(_validator_of(_RequiredUserIn), "unsendable", frozenset()) == {"user"}
    assert getattr(object(), "unsendable", frozenset()) == frozenset()
