"""Tests for SpecRegistry."""

from __future__ import annotations

import pytest

from rest_framework_services import (
    PolymorphicServiceSpec,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
)


def _noop() -> None:
    return None


def _service() -> ServiceSpec[object, object, dict[str, object]]:
    return ServiceSpec(service=_noop)


def _selector() -> SelectorSpec[object, dict[str, object]]:
    return SelectorSpec(kind=SelectorKind.LIST, selector=_noop)


class TestRegister:
    def test_registers_and_reads_back(self) -> None:
        registry = SpecRegistry()
        spec = _service()
        registry.register("refund_order", spec, tags=("write", "admin"))

        entry = registry.get("refund_order")
        assert entry is not None
        assert entry.name == "refund_order"
        assert entry.spec is spec
        assert entry.tags == {"write", "admin"}

    def test_tags_default_to_empty(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector())
        entry = registry.get("list_orders")
        assert entry is not None
        assert entry.tags == frozenset()

    def test_tags_are_deduplicated(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=["read", "read", "public"])
        entry = registry.get("list_orders")
        assert entry is not None
        assert entry.tags == {"read", "public"}

    def test_get_returns_none_for_unknown_name(self) -> None:
        assert SpecRegistry().get("nope") is None

    def test_duplicate_name_raises(self) -> None:
        registry = SpecRegistry()
        registry.register("refund_order", _service())
        with pytest.raises(ValueError, match="already registered"):
            registry.register("refund_order", _selector())

    def test_duplicate_name_does_not_overwrite(self) -> None:
        registry = SpecRegistry()
        first = _service()
        registry.register("refund_order", first)
        with pytest.raises(ValueError):
            registry.register("refund_order", _service())
        entry = registry.get("refund_order")
        assert entry is not None
        assert entry.spec is first

    def test_a_polymorphic_spec_is_rejected_with_a_pointer_at_variants(self) -> None:
        poly = PolymorphicServiceSpec(
            discriminator=lambda **kwargs: "email",
            specs={"email": _service()},
        )
        registry = SpecRegistry()
        with pytest.raises(TypeError, match="Register each variant"):
            registry.register("start_flow", poly)  # type: ignore[arg-type]
        assert len(registry) == 0

    def test_a_non_spec_is_rejected(self) -> None:
        registry = SpecRegistry()
        with pytest.raises(TypeError, match="expected a ServiceSpec or SelectorSpec, got str"):
            registry.register("nonsense", "not a spec")  # type: ignore[arg-type]

    def test_names_are_a_per_registry_namespace(self) -> None:
        # Independent registries are separate surfaces; reusing a name across
        # them is legal and keeps them isolated.
        internal, public = SpecRegistry(), SpecRegistry()
        internal_spec, public_spec = _service(), _service()
        internal.register("purge", internal_spec)
        public.register("purge", public_spec)

        internal_entry, public_entry = internal.get("purge"), public.get("purge")
        assert internal_entry is not None and public_entry is not None
        assert internal_entry.spec is internal_spec
        assert public_entry.spec is public_spec


class TestConstructorSeeding:
    def test_seeds_from_entries(self) -> None:
        spec = _selector()
        registry = SpecRegistry([RegisteredSpec("list_orders", spec, frozenset({"read"}))])
        entry = registry.get("list_orders")
        assert entry is not None
        assert entry.spec is spec
        assert entry.tags == {"read"}

    def test_seeding_validates_duplicates(self) -> None:
        entry = RegisteredSpec("list_orders", _selector())
        with pytest.raises(ValueError, match="already registered"):
            SpecRegistry([entry, entry])

    def test_seeding_validates_the_spec_type(self) -> None:
        with pytest.raises(TypeError, match="expected a ServiceSpec or SelectorSpec"):
            SpecRegistry([RegisteredSpec("bad", object())])  # type: ignore[arg-type]


class TestOrderingAndKinds:
    def test_all_preserves_registration_order(self) -> None:
        registry = SpecRegistry()
        for name in ("c", "a", "b"):
            registry.register(name, _service())
        assert [e.name for e in registry.all()] == ["c", "a", "b"]

    def test_mutations_and_queries_discriminate_by_type(self) -> None:
        registry = SpecRegistry()
        registry.register("refund_order", _service())
        registry.register("list_orders", _selector())
        registry.register("cancel_order", _service())

        assert [e.name for e in registry.mutations()] == ["refund_order", "cancel_order"]
        assert [e.name for e in registry.queries()] == ["list_orders"]

    def test_mutations_and_queries_partition_every_entry(self) -> None:
        # The spec-type check at registration is what guarantees this: no entry
        # can fall outside both accessors.
        registry = SpecRegistry()
        registry.register("refund_order", _service())
        registry.register("list_orders", _selector())

        partitioned = set(registry.mutations()) | set(registry.queries())
        assert partitioned == set(registry.all())
        assert len(registry.mutations()) + len(registry.queries()) == len(registry)


class TestFilteredViews:
    def test_by_tag_returns_matching_entries(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("read", "public"))
        registry.register("refund_order", _service(), tags=("write", "admin"))

        public = registry.by_tag("public")
        assert isinstance(public, SpecRegistry)
        assert [e.name for e in public.all()] == ["list_orders"]

    def test_by_tag_is_a_union_across_tags(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("read",))
        registry.register("refund_order", _service(), tags=("admin",))
        registry.register("purge", _service(), tags=("internal",))

        assert [e.name for e in registry.by_tag("read", "admin").all()] == [
            "list_orders",
            "refund_order",
        ]

    def test_chaining_by_tag_intersects(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("read", "public"))
        registry.register("list_audit", _selector(), tags=("read", "admin"))

        assert [e.name for e in registry.by_tag("read").by_tag("public").all()] == ["list_orders"]

    def test_by_tag_with_no_tags_matches_nothing(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("read",))
        assert len(registry.by_tag()) == 0

    def test_by_tag_preserves_registration_order_and_tags(self) -> None:
        registry = SpecRegistry()
        registry.register("b", _service(), tags=("keep",))
        registry.register("a", _service(), tags=("keep", "extra"))

        view = registry.by_tag("keep")
        assert [e.name for e in view.all()] == ["b", "a"]
        entry = view.get("a")
        assert entry is not None
        assert entry.tags == {"keep", "extra"}

    def test_subset_selects_by_name_in_the_order_given(self) -> None:
        registry = SpecRegistry()
        registry.register("a", _service())
        registry.register("b", _selector())
        registry.register("c", _service())

        assert [e.name for e in registry.subset("c", "a").all()] == ["c", "a"]

    def test_subset_raises_on_an_unknown_name(self) -> None:
        registry = SpecRegistry()
        registry.register("a", _service())
        with pytest.raises(KeyError, match="not registered"):
            registry.subset("a", "typo")

    def test_views_are_snapshots_not_live(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("public",))
        view = registry.by_tag("public")

        registry.register("list_more", _selector(), tags=("public",))

        assert "list_more" in registry
        assert "list_more" not in view

    def test_registering_on_a_view_leaves_the_source_untouched(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", _selector(), tags=("public",))
        view = registry.by_tag("public")

        view.register("view_only", _service())

        assert "view_only" in view
        assert "view_only" not in registry

    def test_views_share_the_spec_objects(self) -> None:
        registry = SpecRegistry()
        spec = _selector()
        registry.register("list_orders", spec, tags=("public",))
        entry = registry.by_tag("public").get("list_orders")
        assert entry is not None
        assert entry.spec is spec


class TestMerge:
    def test_combines_in_order(self) -> None:
        internal, public = SpecRegistry(), SpecRegistry()
        internal.register("purge", _service())
        public.register("list_orders", _selector())

        merged = internal.merge(public)
        assert [e.name for e in merged.all()] == ["purge", "list_orders"]

    def test_merges_several_registries(self) -> None:
        a, b, c = SpecRegistry(), SpecRegistry(), SpecRegistry()
        a.register("a", _service())
        b.register("b", _service())
        c.register("c", _service())
        assert [e.name for e in a.merge(b, c).all()] == ["a", "b", "c"]

    def test_a_name_collision_raises(self) -> None:
        a, b = SpecRegistry(), SpecRegistry()
        a.register("purge", _service())
        b.register("purge", _service())
        with pytest.raises(ValueError, match="already registered"):
            a.merge(b)

    def test_leaves_the_inputs_untouched(self) -> None:
        a, b = SpecRegistry(), SpecRegistry()
        a.register("a", _service())
        b.register("b", _service())

        a.merge(b).register("c", _service())

        assert "c" not in a
        assert "c" not in b
        assert len(a) == len(b) == 1


class TestSpecsDict:
    def test_returns_name_to_spec(self) -> None:
        registry = SpecRegistry()
        mutation, query = _service(), _selector()
        registry.register("refund_order", mutation)
        registry.register("list_orders", query)

        assert registry.specs() == {"refund_order": mutation, "list_orders": query}

    def test_preserves_registration_order(self) -> None:
        registry = SpecRegistry()
        for name in ("c", "a", "b"):
            registry.register(name, _service())
        assert list(registry.specs()) == ["c", "a", "b"]

    def test_is_a_fresh_dict(self) -> None:
        registry = SpecRegistry()
        registry.register("refund_order", _service())

        returned = registry.specs()
        returned["injected"] = _service()

        assert "injected" not in registry
        assert "injected" not in registry.specs()


class TestContainerProtocol:
    def test_len(self) -> None:
        registry = SpecRegistry()
        assert len(registry) == 0
        registry.register("a", _service())
        assert len(registry) == 1

    def test_contains(self) -> None:
        registry = SpecRegistry()
        registry.register("a", _service())
        assert "a" in registry
        assert "b" not in registry

    def test_iterates_entries_in_registration_order(self) -> None:
        registry = SpecRegistry()
        registry.register("b", _service())
        registry.register("a", _selector())
        assert [e.name for e in registry] == ["b", "a"]
        assert all(isinstance(e, RegisteredSpec) for e in registry)


class TestSpecMetadataRoundTrip:
    """``spec.metadata`` survives every filtered view the registry hands back.

    The views snapshot ``RegisteredSpec`` objects and share the spec instances
    themselves, so this holds by construction — pinned here because a future
    rebuild-the-spec optimization would silently drop consumer declarations.
    """

    def test_survives_register_all_by_tag_subset_and_merge(self) -> None:
        spec = ServiceSpec(service=_noop, metadata={"scope": "tenant"})
        other = SpecRegistry()
        other.register("list_orders", _selector(), tags=("read",))
        registry = SpecRegistry()
        registry.register("refund_order", spec, tags=("write",))

        for view in (
            registry,
            registry.by_tag("write"),
            registry.subset("refund_order"),
            registry.merge(other),
        ):
            entry = view.get("refund_order")
            assert entry is not None
            assert entry.spec is spec
            assert entry.spec.metadata == {"scope": "tenant"}

        assert registry.all()[0].spec.metadata == {"scope": "tenant"}
        assert registry.specs()["refund_order"].metadata == {"scope": "tenant"}
        assert registry.mutations()[0].spec.metadata == {"scope": "tenant"}

    def test_reaches_a_registry_consumer_through_entry_spec(self) -> None:
        registry = SpecRegistry()
        registry.register("list_orders", SelectorSpec(kind=SelectorKind.LIST, metadata={"a": 1}))
        assert [e.spec.metadata for e in registry.queries()] == [{"a": 1}]

    def test_tags_and_metadata_are_independent(self) -> None:
        registry = SpecRegistry()
        registry.register("refund_order", _service(), tags=("write",))
        entry = registry.get("refund_order")
        assert entry is not None
        assert entry.tags == {"write"}
        assert entry.spec.metadata is None
