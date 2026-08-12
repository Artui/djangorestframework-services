"""The stable dispatch surface: blessed top-level re-exports.

Stable since 0.17: the dispatch leaves alternate transports build on are part
of the documented public API. This test pins the contract: every blessed
symbol is importable from the top-level package and is *the same object*
as its leaf-module home (no divergent copies). The private ``_compat``
package was removed in the same release — downstreams re-point their
imports here when they bump past 0.17.
"""

from __future__ import annotations

import rest_framework_services as pkg
from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.dispatch.enforce_permissions import enforce_permissions
from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.is_async import is_async
from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.selectors.utils import (
    apply_queryset_shaping,
    arun_selector,
    is_queryset,
    run_selector,
)
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.registered_spec import RegisteredSpec
from rest_framework_services.views.mutation.utils import (
    build_input_serializer,
    build_input_serializer_from_data,
    resolve_mutation_instance,
    validate_input,
)
from rest_framework_services.views.utils import resolve_callable_kwargs

# Blessed name → the leaf-module original it must alias.
_SURFACE = {
    "RegisteredSpec": RegisteredSpec,
    "SpecRegistry": SpecRegistry,
    "adispatch_spec": adispatch_spec,
    "apply_queryset_shaping": apply_queryset_shaping,
    "arun_selector": arun_selector,
    "arun_service": arun_service,
    "build_input_serializer": build_input_serializer,
    "build_input_serializer_from_data": build_input_serializer_from_data,
    "build_offline_context": build_offline_context,
    "dispatch_spec": dispatch_spec,
    "enforce_permissions": enforce_permissions,
    "filterset_to_json_schema": filterset_to_json_schema,
    "is_async": is_async,
    "is_queryset": is_queryset,
    "output_to_json_schema": output_to_json_schema,
    "render_spec_output": render_spec_output,
    "resolve_callable_kwargs": resolve_callable_kwargs,
    "resolve_mutation_instance": resolve_mutation_instance,
    "run_selector": run_selector,
    "run_service": run_service,
    "serializer_to_json_schema": serializer_to_json_schema,
    "spec_to_json_schema": spec_to_json_schema,
    "validate_input": validate_input,
}


def test_every_blessed_symbol_is_a_top_level_export() -> None:
    for name, original in _SURFACE.items():
        assert name in pkg.__all__, f"{name} missing from __all__"
        assert getattr(pkg, name) is original, f"{name} diverged from its leaf module"


def test_the_compat_package_is_gone() -> None:
    # Removed in 0.17 — anything still importing it should fail loudly here
    # rather than in a downstream release.
    import importlib.util

    assert importlib.util.find_spec("rest_framework_services._compat") is None


def test_no_export_is_shadowed_by_its_own_submodule() -> None:
    """Every re-exported name resolves to a value, never to a module object.

    **This hazard is real and has already been hit once.** ``import pkg.mod``
    binds ``mod`` as an attribute of ``pkg``, so a package ``__init__`` that
    does ``from pkg.mod import mod`` (function and module sharing a name — the
    one-symbol-per-file convention makes that the *normal* case here) can have
    its function binding overwritten by the submodule, if some *other* import
    on a later line pulls ``pkg.mod`` in for the first time. The symptom is a
    module where a callable was expected, at import time, far from the edit
    that caused it.

    ``dispatch/__init__.py`` carries a hand-written comment pinning one such
    ordering. ⇒ *an invariant maintained by a comment is maintained until
    someone sorts the imports.* This asserts it for every export in every
    package instead, so a future reordering fails here rather than downstream.
    """
    import importlib
    from types import ModuleType

    packages = [
        "rest_framework_services",
        "rest_framework_services.dispatch",
        "rest_framework_services.mutations",
        "rest_framework_services.selectors",
        "rest_framework_services.services",
        "rest_framework_services.types",
    ]
    shadowed: list[str] = []
    for name in packages:
        module = importlib.import_module(name)
        for export in getattr(module, "__all__", ()):
            if isinstance(getattr(module, export), ModuleType):
                shadowed.append(f"{name}.{export}")
    assert shadowed == []
