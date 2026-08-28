"""``RegisteredSpec`` — one named, tagged entry in a ``SpecRegistry``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.agent_contract import AgentContract
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass(frozen=True)
class RegisteredSpec:
    """A spec under its canonical name, with free-form tags.

    The value type held by
    [`SpecRegistry`][rest_framework_services.registry.spec_registry.SpecRegistry]. It
    carries only the part of a "tool" that is **invariant across transports** —
    which spec, what it is called, and how it is grouped. Per-transport knobs
    (MCP annotations and output schemas, Pydantic-AI pagination or query-param
    registrations, …) stay at the binding that configures them.

    Fields:

    - **``name``** — the canonical name for this operation. Unique within the registry
      that holds it; two independent registries are separate namespaces and may reuse a
      name.
    - **``spec``** — a
      [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] (a
      mutation) or a
      [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] (a
      read). The kind is deliberately **not** stored: it is derived by ``isinstance``
      wherever it is needed, so a stored discriminator can never drift from the object
      it describes.
    - **``tags``** — a frozen set of free-form labels used to derive filtered views
      (``registry.by_tag("public")``). Tags carry boolean-ish facts — ``"read"``,
      ``"admin"``, ``"destructive"`` — that every transport can interpret in its own
      vocabulary. Structured, transport-specific payloads do not belong in a tag string;
      they belong at the binding.
    - **``agent_contract``** — an optional
      [`AgentContract`][rest_framework_services.types.agent_contract.AgentContract]:
      what a transport with **no HTTP request** has to be told, because the URLconf
      and query string told an HTTP one for free. One typed slot rather than loose
      fields, so this stays an index rather than becoming a configuration object.

    **Note: "invariant across transports" is not the same test as "which transport
    configures it"**, and reading it as the latter is what put these declarations
    at the binding. `icons` and `annotations` are genuinely MCP's. A `UrlKwarg`
    is not: every off-HTTP transport needs the identical one, and none of them is
    where it should be declared.
    """

    name: str
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]
    tags: frozenset[str] = frozenset()
    agent_contract: AgentContract | None = None


__all__ = ["RegisteredSpec"]
