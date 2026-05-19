"""Static typing contract for the mutation helpers.

The mutation helpers thread a ``ModelT = TypeVar("ModelT", bound=Model)``
through their signatures so callers that pass a concrete model class get a
``ChangeResult[SomeModel]`` back instead of a bare ``ChangeResult``.

We can't observe a TypeVar's resolution at runtime (it's erased to its
bound), but we can lock in the *shape* — the return is ``ChangeResult[T]``
parametrised by the same TypeVar declared in :mod:`change_result` — using
``typing.get_type_hints``.
"""

from __future__ import annotations

from typing import get_args, get_origin, get_type_hints

from rest_framework_services.mutations.acreate_from_input import acreate_from_input
from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input
from rest_framework_services.mutations.create_from_input import create_from_input
from rest_framework_services.mutations.update_from_input import update_from_input
from rest_framework_services.types.change_result import ChangeResult, ModelT
from tests.testapp.models import Author


class TestMutationHelperReturnTypes:
    def test_create_from_input_returns_change_result_of_model_t(self) -> None:
        return_hint = get_type_hints(create_from_input)["return"]
        assert get_origin(return_hint) is ChangeResult
        assert get_args(return_hint) == (ModelT,)

    def test_update_from_input_returns_change_result_of_model_t(self) -> None:
        return_hint = get_type_hints(update_from_input)["return"]
        assert get_origin(return_hint) is ChangeResult
        assert get_args(return_hint) == (ModelT,)

    def test_acreate_from_input_returns_change_result_of_model_t(self) -> None:
        return_hint = get_type_hints(acreate_from_input)["return"]
        assert get_origin(return_hint) is ChangeResult
        assert get_args(return_hint) == (ModelT,)

    def test_aupdate_from_input_returns_change_result_of_model_t(self) -> None:
        return_hint = get_type_hints(aupdate_from_input)["return"]
        assert get_origin(return_hint) is ChangeResult
        assert get_args(return_hint) == (ModelT,)


class TestMutationHelperModelParameter:
    def test_create_from_input_model_is_type_of_model_t(self) -> None:
        model_hint = get_type_hints(create_from_input)["model"]
        assert get_origin(model_hint) is type
        assert get_args(model_hint) == (ModelT,)

    def test_acreate_from_input_model_is_type_of_model_t(self) -> None:
        model_hint = get_type_hints(acreate_from_input)["model"]
        assert get_origin(model_hint) is type
        assert get_args(model_hint) == (ModelT,)

    def test_update_from_input_instance_is_model_t(self) -> None:
        instance_hint = get_type_hints(update_from_input)["instance"]
        assert instance_hint is ModelT

    def test_aupdate_from_input_instance_is_model_t(self) -> None:
        instance_hint = get_type_hints(aupdate_from_input)["instance"]
        assert instance_hint is ModelT


class TestChangeResultGeneric:
    def test_change_result_is_generic_over_model_t(self) -> None:
        instance_hint = get_type_hints(ChangeResult)["instance"]
        assert instance_hint is ModelT

    def test_parametrised_change_result_round_trips(self) -> None:
        """``ChangeResult[ConcreteModel]`` evaluates and exposes the arg."""
        parametrised = ChangeResult[Author]
        assert get_origin(parametrised) is ChangeResult
        assert get_args(parametrised) == (Author,)
