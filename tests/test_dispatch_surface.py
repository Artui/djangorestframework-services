"""The stable dispatch surface: blessed re-exports + compat shims.

SURF-1 (0.17): the dispatch leaves alternate transports build on are part
of the documented public API. This test pins two contracts:

- every blessed symbol is importable from the top-level package and is
  *the same object* as its leaf-module home (no divergent copies);
- the pre-0.17 ``_compat`` leaf paths keep working — downstreams
  (drf-mcp ≤0.7) import them directly.
"""

from __future__ import annotations

import rest_framework_services as pkg
from rest_framework_services._compat.arun_service import arun_service as compat_arun_service
from rest_framework_services._compat.run_service import run_service as compat_run_service
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


def test_compat_shims_alias_the_promoted_runners() -> None:
    # drf-mcp ≤0.7 imports these leaf paths; the shims must stay aliases of
    # the promoted implementations, not copies.
    assert compat_run_service is run_service
    assert compat_arun_service is arun_service


def test_compat_package_init_keeps_exporting_the_runners() -> None:
    from rest_framework_services import _compat

    assert _compat.run_service is run_service
    assert _compat.arun_service is arun_service
