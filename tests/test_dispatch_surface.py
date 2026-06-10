"""The stable dispatch surface: blessed top-level re-exports.

SURF-1 (0.17): the dispatch leaves alternate transports build on are part
of the documented public API. This test pins the contract: every blessed
symbol is importable from the top-level package and is *the same object*
as its leaf-module home (no divergent copies). The private ``_compat``
package was removed in the same release — downstreams re-point their
imports here when they bump past 0.17.
"""

from __future__ import annotations

import rest_framework_services as pkg
from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import (
    apply_queryset_shaping,
    arun_selector,
    is_queryset,
    run_selector,
)
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.views.mutation.utils import (
    build_input_serializer,
    resolve_mutation_instance,
    validate_input,
)
from rest_framework_services.views.utils import resolve_callable_kwargs

# Blessed name → the leaf-module original it must alias.
_SURFACE = {
    "apply_queryset_shaping": apply_queryset_shaping,
    "arun_selector": arun_selector,
    "arun_service": arun_service,
    "build_input_serializer": build_input_serializer,
    "is_async": is_async,
    "is_queryset": is_queryset,
    "resolve_callable_kwargs": resolve_callable_kwargs,
    "resolve_mutation_instance": resolve_mutation_instance,
    "run_selector": run_selector,
    "run_service": run_service,
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
