"""Every value and exception django-service-specs defines is drfs' too, by identity.

drfs keeps each name at its own module path, and the module re-exports the
kernel's object rather than defining a twin. Identity, not equality, is the
claim, because it is what a consumer meets first: an ``except ServiceError``
written against either package catches what the other raises, and an
``isinstance`` against a drfs relation spec answers for one the kernel built.

The behaviour of each object is the kernel's, and its tests are the kernel's.
What stays here is the one row per module that would fail if drfs defined the
name again, and, in the test file beside each module, the behaviour a drfs
caller sees change because the object did.
"""

from __future__ import annotations

import importlib

import pytest

# (drfs module, kernel module, name). The kernel paths are the leaf modules
# the kernel pins for drfs, never its package root, so a move there fails here
# by name rather than resolving through a re-export.
REEXPORTS: list[tuple[str, str, str]] = [
    # Exceptions.
    (
        "rest_framework_services.exceptions.action_unavailable",
        "django_service_specs.services.action_unavailable",
        "ActionUnavailable",
    ),
    (
        "rest_framework_services.exceptions.additional_input_required",
        "django_service_specs.services.additional_input_required",
        "AdditionalInputRequired",
    ),
    (
        "rest_framework_services.exceptions.service_conflict",
        "django_service_specs.services.service_conflict",
        "ServiceConflict",
    ),
    (
        "rest_framework_services.exceptions.service_error",
        "django_service_specs.services.service_error",
        "ServiceError",
    ),
    (
        "rest_framework_services.exceptions.service_not_found",
        "django_service_specs.services.service_not_found",
        "ServiceNotFound",
    ),
    (
        "rest_framework_services.exceptions.service_validation_error",
        "django_service_specs.services.service_validation_error",
        "ServiceValidationError",
    ),
    # Values.
    (
        "rest_framework_services.types.audience_projection",
        "django_service_specs.output.audience_projection",
        "AudienceProjection",
    ),
    (
        "rest_framework_services.types.change_result",
        "django_service_specs.mutations.change_result",
        "ChangeResult",
    ),
    (
        "rest_framework_services.types.child_collection_change",
        "django_service_specs.mutations.child_collection_change",
        "ChildCollectionChange",
    ),
    (
        "rest_framework_services.types.field_audience",
        "django_service_specs.types.field_audience",
        "FieldAudience",
    ),
    (
        "rest_framework_services.types.field_change",
        "django_service_specs.mutations.field_change",
        "FieldChange",
    ),
    (
        "rest_framework_services.types.field_marking",
        "django_service_specs.output.field_marking",
        "FieldMarking",
    ),
    (
        "rest_framework_services.types.output_page",
        "django_service_specs.types.output_page",
        "OutputPage",
    ),
    (
        "rest_framework_services.types.progress_reporter",
        "django_service_specs.types.progress_reporter",
        "ProgressReporter",
    ),
    (
        "rest_framework_services.types.related_object_change",
        "django_service_specs.mutations.related_object_change",
        "RelatedObjectChange",
    ),
    (
        "rest_framework_services.types.relation_mode",
        "django_service_specs.types.relation_mode",
        "RelationMode",
    ),
    (
        "rest_framework_services.types.relation_orphan",
        "django_service_specs.types.relation_orphan",
        "RelationOrphan",
    ),
    (
        "rest_framework_services.types.relation_outcome",
        "django_service_specs.types.relation_outcome",
        "RelationOutcome",
    ),
    (
        "rest_framework_services.types.relation_phase",
        "django_service_specs.types.relation_phase",
        "RelationPhase",
    ),
    (
        "rest_framework_services.types.selector_kind",
        "django_service_specs.types.selector_kind",
        "SelectorKind",
    ),
    ("rest_framework_services.types.unset", "django_service_specs.types.unset", "UNSET"),
    ("rest_framework_services.types.unset", "django_service_specs.types.unset", "UnsetType"),
    (
        "rest_framework_services.types.affordance",
        "django_service_specs.types.affordance",
        "Affordance",
    ),
    (
        "rest_framework_services.types.value_formatter",
        "django_service_specs.types.value_formatter",
        "ValueFormatter",
    ),
    # The relation layer's declarations.
    (
        "rest_framework_services.types.child_spec",
        "django_service_specs.relations.child_spec",
        "ChildSpec",
    ),
    (
        "rest_framework_services.types.forward_relation_spec",
        "django_service_specs.relations.forward_relation_spec",
        "ForwardRelationSpec",
    ),
    (
        "rest_framework_services.types.generic_relation_spec",
        "django_service_specs.relations.generic_relation_spec",
        "GenericRelationSpec",
    ),
    (
        "rest_framework_services.types.many_to_many_spec",
        "django_service_specs.relations.many_to_many_spec",
        "ManyToManySpec",
    ),
    (
        "rest_framework_services.types.reverse_one_to_one_spec",
        "django_service_specs.relations.reverse_one_to_one_spec",
        "ReverseOneToOneSpec",
    ),
    (
        "rest_framework_services.types.relation_spec",
        "django_service_specs.relations.relation_spec",
        "RelationSpec",
    ),
    # The keyword pool's seed registry.
    (
        "rest_framework_services.types.pool_seeds",
        "django_service_specs.pool.pool_seeds",
        "PoolSeeds",
    ),
    (
        "rest_framework_services.types.pool_seeds",
        "django_service_specs.pool.pool_seeds",
        "DEFAULT_POOL_SEEDS",
    ),
    (
        "rest_framework_services.types.reserved_pool_seeds",
        "django_service_specs.pool.reserved_pool_seeds",
        "RESERVED_POOL_SEEDS",
    ),
    # What dispatch answers, and how it treats an argument nothing declares.
    (
        "rest_framework_services.types.dispatch_result",
        "django_service_specs.dispatch.dispatch_result",
        "DispatchResult",
    ),
    (
        "rest_framework_services.types.unknown_arguments",
        "django_service_specs.types.unknown_arguments",
        "UnknownArguments",
    ),
    # The registry a project declares its operations in once.
    (
        "rest_framework_services.types.registered_spec",
        "django_service_specs.registry.registered_spec",
        "RegisteredSpec",
    ),
    (
        "rest_framework_services.registry.spec_registry",
        "django_service_specs.registry.spec_registry",
        "SpecRegistry",
    ),
]


@pytest.mark.parametrize(
    ("drfs_module", "kernel_module", "name"),
    REEXPORTS,
    ids=[f"{row[0].rsplit('.', 1)[1]}.{row[2]}" for row in REEXPORTS],
)
def test_the_drfs_name_is_the_kernels_object(
    drfs_module: str, kernel_module: str, name: str
) -> None:
    drfs_object = getattr(importlib.import_module(drfs_module), name)
    kernel_object = getattr(importlib.import_module(kernel_module), name)

    assert drfs_object is kernel_object
