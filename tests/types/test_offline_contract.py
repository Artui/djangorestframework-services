"""What a transport with no HTTP request has to be told, declared once.

Over HTTP a nested route's captures reach a spec through ``view.kwargs`` because
the URLconf declared them, and read-shaping params reach a serializer through
``request.query_params`` because the query string carried them. A spec mounted on
a view is already complete.

Off HTTP there is no route and no query string, so somebody has to say what they
would have contained -- and every off-HTTP transport needs the *identical*
answer. A project running MCP and an in-process toolset used to declare it twice,
in two shapes, with nothing comparing them.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from rest_framework_services import (
    FieldMarking,
    OfflineContract,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    SpecRegistry,
    UrlKwarg,
)


class _Invoice(serializers.Serializer):
    etag = serializers.CharField()


def _spec(**declared: Any) -> SelectorSpec:
    # Open on purpose: the registry refuses a spec that declares no permissions.
    # The contract is declared on the spec (``reads``, ``field_audiences``), and
    # the entry answers it by asking the spec, since ``register`` takes none.
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda **_: [], permission_classes=[], **declared
    )


class TestTheDefault:
    def test_an_entry_declares_nothing_unless_asked(self) -> None:
        # A spec declaring neither ``reads`` nor ``field_audiences`` answers no
        # contract, as an entry registered without one did.
        registry = SpecRegistry()
        registry.register("list_widgets", _spec())

        entry = registry.get("list_widgets")
        assert entry is not None
        assert entry.agent_contract is None


class TestWhatItCarries:
    def test_url_kwargs_and_query_params_ride_through_verbatim(self) -> None:
        project, fields = UrlKwarg("project_pk"), QueryParam("fields")
        registry = SpecRegistry()
        registry.register("list_widgets", _spec(reads=(project, fields)))

        entry = registry.get("list_widgets")
        assert entry is not None
        assert entry.agent_contract == OfflineContract(
            url_kwargs=(project,), query_params=(fields,)
        )

    def test_it_is_frozen_like_every_other_wire_shape_here(self) -> None:
        import dataclasses

        import pytest

        contract = OfflineContract(url_kwargs=(UrlKwarg("project_pk"),))

        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.url_kwargs = ()  # type: ignore[misc]


class TestFieldAudiences:
    """The override lives with the thing it overrides.

    ``FieldAudience`` already settles which bucket this is in: *the axis is
    audience, not protocol* -- an MCP server and an in-process toolset want the
    same thing as each other. If the two are one audience, an override of what
    that audience sees cannot legitimately differ between them, so it is
    structural rather than policy.
    """

    def test_it_rides_the_contract(self) -> None:
        overrides = {"etag": FieldMarking()}
        registry = SpecRegistry()
        registry.register(
            "lookup_invoice", _spec(field_audiences=overrides, output_serializer=_Invoice)
        )

        entry = registry.get("lookup_invoice")
        assert entry is not None
        assert entry.agent_contract is not None
        assert entry.agent_contract.field_audiences == overrides

    def test_declaring_none_is_the_default(self) -> None:
        # The serializer's own markings stay authoritative unless overridden.
        assert OfflineContract().field_audiences is None


class TestItSurvivesTheRegistryOperations:
    def test_by_tag_carries_the_contract_with_the_entry(self) -> None:
        # The property that makes the registry a viable home: a filtered view is
        # built from RegisteredSpec objects, so anything on the entry survives.
        registry = SpecRegistry()
        registry.register("list_widgets", _spec(reads=(UrlKwarg("project_pk"),)), tags=("public",))
        registry.register("other", _spec(), tags=("internal",))

        filtered = registry.by_tag("public")

        entry = filtered.get("list_widgets")
        assert entry is not None
        assert entry.agent_contract == OfflineContract(url_kwargs=(UrlKwarg("project_pk"),))

    def test_subset_carries_it_too(self) -> None:
        registry = SpecRegistry()
        registry.register("list_widgets", _spec(reads=(QueryParam("fields"),)))
        registry.register("other", _spec())

        entry = registry.subset("list_widgets").get("list_widgets")

        assert entry is not None
        assert entry.agent_contract == OfflineContract(query_params=(QueryParam("fields"),))


class TestWhatItDeliberatelyDoesNotCarry:
    def test_no_bounds_or_strictness_knobs(self) -> None:
        """Those legitimately differ between mounts, so sharing them is wrong.

        A publicly exposed MCP endpoint and an in-process toolset have different
        risk profiles; one shared result-size cap or unknown-argument policy
        would be a regression rather than a simplification. This carries only
        what *cannot* differ.
        """
        import dataclasses

        fields = {f.name for f in dataclasses.fields(OfflineContract)}

        assert fields == {"url_kwargs", "query_params", "field_audiences"}

    def test_no_ordering(self) -> None:
        """Sorting is already declared once, on the spec's own ``filter_set``.

        It is also the family's only deprecation, so putting it here would
        enshrine in a new API exactly what is scheduled for removal.
        """
        import dataclasses

        assert "ordering_fields" not in {f.name for f in dataclasses.fields(OfflineContract)}
