# Types

Most values here are django-service-specs' own, re-exported at the drfs path
shown, so each drfs name is the kernel's object and the documentation rendered
for it is the kernel's: `SelectorKind`, `Affordance`, `ChangeResult`,
`FieldChange`, the relation declarations (`ChildSpec`, `ForwardRelationSpec`,
`ReverseOneToOneSpec`, `GenericRelationSpec`, `ManyToManySpec`, `RelationSpec`)
and their outcomes, `UNSET`, `ProgressReporter`, the pool seed registry
(`PoolSeeds`, `DEFAULT_POOL_SEEDS`, `RESERVED_POOL_SEEDS`),
`FieldAudience`, `FieldMarking`, `ValueFormatter` and `AudienceProjection`. The
declarations that carry serializers and permission classes (`ServiceSpec`,
`SelectorSpec`), the offline request and its channels, the input markers and
`MARKING` are drfs' own.

Being the kernel's changes what a few of them refuse, each where it is built:

- An `Affordance` whose callable `when` is `async def`, or reads `result` or
  `queryset`, is refused with `ImproperlyConfigured`, as one reading `data` or
  `instance` already was.
- A relation declaration takes only its model, and its foreign key where it has
  one, by position; every other option is keyword-only. `relations=` is the one
  spelling for nested relations: the `children=` alias is gone.
- `PoolSeeds` checks a registration against the kernel's reserved set, `user`,
  `data`, `instance`, `collection`, `result`, `queryset` and `progress`: a seed
  named `result` or `queryset` is refused, one named `request` or `serializer`
  can be registered, and an `async def` resolver is refused with
  `ImproperlyConfigured`.
- `ValueFormatter.timestamp()` reports a string shaped like a date-time that
  names no real moment (`2026-02-30T12:00:00Z`) as it was stored, rather than
  raising.

## `SelectorKind`

::: rest_framework_services.types.selector_kind.SelectorKind

## `SelectorSpec`

::: rest_framework_services.types.selector_spec.SelectorSpec

## `ServiceSpec`

::: rest_framework_services.types.service_spec.ServiceSpec

## `Affordance`

::: rest_framework_services.types.affordance.Affordance

## `AffordanceDeclarer`

::: rest_framework_services.types.affordance_declarer.AffordanceDeclarer

## `PolymorphicServiceSpec`

::: rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec

## `ChangeResult`

::: rest_framework_services.types.change_result.ChangeResult

## `FieldChange`

::: rest_framework_services.types.field_change.FieldChange

## `ChildSpec`

::: rest_framework_services.types.child_spec.ChildSpec

## `ChildCollectionChange`

::: rest_framework_services.types.child_collection_change.ChildCollectionChange

## `RelationSpec`

::: rest_framework_services.types.relation_spec.RelationSpec

## `RelationPhase`

::: rest_framework_services.types.relation_phase.RelationPhase

## `RelationOrphan`

::: rest_framework_services.types.relation_orphan.RelationOrphan

## `ForwardRelationSpec`

::: rest_framework_services.types.forward_relation_spec.ForwardRelationSpec

## `ReverseOneToOneSpec`

::: rest_framework_services.types.reverse_one_to_one_spec.ReverseOneToOneSpec

## `GenericRelationSpec`

::: rest_framework_services.types.generic_relation_spec.GenericRelationSpec

## `ManyToManySpec`

::: rest_framework_services.types.many_to_many_spec.ManyToManySpec

## `RelatedObjectChange`

::: rest_framework_services.types.related_object_change.RelatedObjectChange

## `UNSET`

::: rest_framework_services.types.unset.UNSET

::: rest_framework_services.types.unset.UnsetType

## `NoInput`

::: rest_framework_services.types.no_input.NoInput

## `HttpExtras`

::: rest_framework_services.types.http_extras.HttpExtras

## `OfflineContext`

::: rest_framework_services.types.offline_context.OfflineContext

## `InputRequired`

Dispatch refuses a missing marked key as the kernel's `InvalidArguments`,
`{"code": ["This field is required."]}`, wherever the key is caller input: a
selector's, a target lookup's, and a serializer-less
service's own parameters under `SPREAD_AUTHOR_WINS`. Under `AUTO` or `BUNDLE`
such a service's parameters are no caller input, so a marker on one is not
enforced. See
[what a caller could fill](../recipes/off-http-inputs.md#requiredness-inputrequired).

::: rest_framework_services.types.input_required

## `NotClientInput`

::: rest_framework_services.types.not_client_input

## `InputDescription`

::: rest_framework_services.types.input_description

## `read_input_description`

::: rest_framework_services.types.read_input_description.read_input_description

## `UrlKwarg`

::: rest_framework_services.types.url_kwarg.UrlKwarg

## `QueryParam`

::: rest_framework_services.types.query_param.QueryParam

## `validate_channel_names`

::: rest_framework_services.types.validate_channel_names.validate_channel_names

## `ProgressReporter`

::: rest_framework_services.types.progress_reporter.ProgressReporter

## `RESERVED_POOL_SEEDS`

::: rest_framework_services.types.reserved_pool_seeds

## `PoolSeeds`

::: rest_framework_services.types.pool_seeds

## `FieldAudience`

::: rest_framework_services.types.field_audience.FieldAudience

## `MARKING`

::: rest_framework_services.types.field_marking.MARKING

## `FieldMarking`

::: rest_framework_services.types.field_marking.FieldMarking

## `ValueFormatter`

::: rest_framework_services.types.value_formatter.ValueFormatter

## `AudienceProjection`

::: rest_framework_services.types.audience_projection.AudienceProjection
