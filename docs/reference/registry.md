# Spec registry

A named, taggable home for a project's spec set, so each transport reads one
source instead of enumerating the same specs again. Both symbols are importable
from the top-level package.

For the task-shaped walkthrough — where the instance lives, multiple registries,
filtered views — see the recipe
[Declare specs once, project them to many transports](../recipes/spec-registry.md).

## `SpecRegistry`

::: rest_framework_services.registry.spec_registry.SpecRegistry

## `RegisteredSpec`

::: rest_framework_services.types.registered_spec.RegisteredSpec

## `AgentContract`

::: rest_framework_services.types.agent_contract.AgentContract
