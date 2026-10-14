"""``reads`` and ``field_audiences`` on both spec classes, and the contract they answer.

A spec declares what an off-HTTP transport has to be told about its route and
query string itself, so the declaration travels with the spec and a registry
entry only asks for it.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    FieldMarking,
    OfflineContract,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    UrlKwarg,
)


def _selector(**fields: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: [], **fields)


def _service(**fields: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=lambda **_: None, **fields)


_BUILDERS = [pytest.param(_selector, id="selector"), pytest.param(_service, id="service")]


@pytest.mark.parametrize("build", _BUILDERS)
class TestReads:
    def test_default_to_nothing_declared(self, build: Any) -> None:
        spec = build()

        assert (spec.reads, spec.field_audiences) == ((), None)

    def test_are_held_as_a_tuple(self, build: Any) -> None:
        declared = [UrlKwarg("project_pk"), QueryParam("fields")]

        assert build(reads=declared).reads == tuple(declared)

    @pytest.mark.parametrize(
        "reads", ["project_pk", UrlKwarg("project_pk"), QueryParam("fields"), {"a": 1}]
    )
    def test_a_lone_entry_or_a_string_is_refused(self, build: Any, reads: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"Spec\.reads takes a sequence"):
            build(reads=reads)

    def test_an_entry_of_another_type_is_refused(self, build: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"Spec\.reads\[1\] must be a UrlKwarg"):
            build(reads=[UrlKwarg("a"), "b"])

    def test_a_name_declared_twice_is_refused(self, build: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="declares 'a' twice"):
            build(reads=[UrlKwarg("a"), QueryParam("a")])


@pytest.mark.parametrize("build", _BUILDERS)
class TestTheAgentContract:
    def test_is_none_when_nothing_is_declared(self, build: Any) -> None:
        assert build().agent_contract() is None
        assert build(field_audiences={}).agent_contract() is None

    def test_equals_the_contract_the_same_declarations_registered(self, build: Any) -> None:
        # What ``register(..., agent_contract=...)`` was handed for these
        # declarations, built the way that call took it: the route captures and
        # the query params apart, in declaration order.
        project, team = UrlKwarg("project_pk", type="integer"), UrlKwarg("team_pk")
        fields = QueryParam("fields", description="Fields to return.")
        audiences = {"title": FieldMarking.hidden()}
        registered = OfflineContract(
            url_kwargs=(project, team), query_params=(fields,), field_audiences=audiences
        )

        spec = build(reads=[project, fields, team], field_audiences=audiences)

        assert spec.agent_contract() == registered

    def test_audiences_alone_are_a_contract(self, build: Any) -> None:
        audiences = {"title": FieldMarking.hidden()}

        assert build(field_audiences=audiences).agent_contract() == OfflineContract(
            field_audiences=audiences
        )

    def test_reads_alone_carry_no_audiences(self, build: Any) -> None:
        assert build(reads=[QueryParam("q")]).agent_contract() == OfflineContract(
            query_params=(QueryParam("q"),)
        )

    def test_a_registry_entry_answers_it(self, build: Any) -> None:
        registry = SpecRegistry()
        registry.register("a", build(reads=[UrlKwarg("project_pk")], permission_classes=[]))

        entry = registry.get("a")
        assert entry is not None
        assert entry.agent_contract == OfflineContract(url_kwargs=(UrlKwarg("project_pk"),))
