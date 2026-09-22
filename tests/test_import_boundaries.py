"""Where the package reaches into DRF and into its own HTTP views, held as ratchets.

``dispatch_spec`` describes itself as the single transport-neutral execution path,
and a non-HTTP caller (an MCP handler, an agent toolset, a management command, a
worker) runs it with no request and no view. That claim is only as true as the
imports behind it: every module outside the HTTP transport that imports
``rest_framework`` or ``rest_framework_services.views`` is a place the neutral core
still depends on the transport it is meant to be independent of.

These are not bans. Most entries below are legitimate today — the spec
dataclasses type ``permission_classes`` as DRF's ``BasePermission``, and the
keyword-pool resolver lives in ``views/utils.py`` for historical reasons. What the
tests forbid is **growth without a decision**: an import that is not listed fails,
and so does a listed import that no longer exists, so the lists can only shrink
and every shrink is recorded here rather than lost.

Each allowlist is keyed by module path relative to the package, with the modules
it imports. A ``from pkg import name`` counts as importing ``pkg.name`` when that
is a module, so ``from rest_framework import serializers`` is recorded as
``rest_framework.serializers`` rather than as the whole of DRF.

The walk reads every ``import`` in the file — top level, function-local and under
``TYPE_CHECKING`` alike — because a deferred import is still a dependency; it is
only a later one.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import rest_framework_services

PACKAGE_ROOT = Path(rest_framework_services.__file__).parent

# The HTTP transport. Importing DRF, or each other, is what these are for.
# ``openapi`` is the drf-spectacular integration, which exists only over HTTP.
TRANSPORT_PACKAGES = frozenset({"views", "viewsets", "openapi"})

# The package root re-exports the whole public surface, the HTTP views included,
# so its imports say nothing about which layer depends on which.
EXEMPT_MODULES = frozenset({"__init__.py"})

VIEWS_PREFIXES = ("rest_framework_services.views", "rest_framework_services.viewsets")
DRF_PREFIXES = ("rest_framework",)

# Modules outside the transport that import the HTTP views, and what they import.
# The standing reason for most of them is ``resolve_callable_kwargs`` — the
# keyword-pool resolver, the most transport-neutral code in the package, filed in
# ``views/utils.py`` beside the view helpers that share its module.
ALLOWED_VIEWS_IMPORTS: dict[str, frozenset[str]] = {
    "dispatch/adispatch_spec.py": frozenset(
        {
            "rest_framework_services.views.mutation.resolve_success_status",
            "rest_framework_services.views.mutation.utils",
        }
    ),
    "dispatch/base_pool.py": frozenset({"rest_framework_services.views.utils"}),
    "dispatch/dispatch_spec.py": frozenset(
        {
            "rest_framework_services.views.mutation.resolve_success_status",
            "rest_framework_services.views.mutation.utils",
        }
    ),
    "dispatch/utils.py": frozenset({"rest_framework_services.views.utils"}),
    "mutations/utils.py": frozenset({"rest_framework_services.views.utils"}),
    "selectors/acall_selector.py": frozenset({"rest_framework_services.views.utils"}),
    "selectors/call_selector.py": frozenset({"rest_framework_services.views.utils"}),
    "selectors/utils.py": frozenset({"rest_framework_services.views.utils"}),
    "services/acall_service.py": frozenset(
        {
            "rest_framework_services.views.mutation.map_service_error",
            "rest_framework_services.views.utils",
        }
    ),
    "services/call_service.py": frozenset(
        {
            "rest_framework_services.views.mutation.map_service_error",
            "rest_framework_services.views.utils",
        }
    ),
}

# Modules outside the transport that import DRF, and which of its modules.
ALLOWED_DRF_IMPORTS: dict[str, frozenset[str]] = {
    "audience/build_audience_projection.py": frozenset({"rest_framework.serializers"}),
    "dispatch/build_offline_context.py": frozenset({"rest_framework.request"}),
    "dispatch/enforce_permissions.py": frozenset({"rest_framework.exceptions"}),
    "dispatch/renderable_serializer_class.py": frozenset({"rest_framework.serializers"}),
    "dispatch/utils.py": frozenset(
        {
            "rest_framework.exceptions",
            "rest_framework.serializers",
            "rest_framework.settings",
        }
    ),
    "jsonschema/output_to_json_schema.py": frozenset({"rest_framework.serializers"}),
    "jsonschema/serializer_to_json_schema.py": frozenset({"rest_framework.serializers"}),
    "jsonschema/utils.py": frozenset(
        {
            "rest_framework.fields",
            "rest_framework.serializers",
        }
    ),
    "mutations/utils.py": frozenset({"rest_framework.exceptions"}),
    "selectors/acall_selector.py": frozenset({"rest_framework.request"}),
    "selectors/call_selector.py": frozenset({"rest_framework.request"}),
    "selectors/utils.py": frozenset(
        {
            "rest_framework.exceptions",
            "rest_framework.request",
        }
    ),
    "services/acall_service.py": frozenset({"rest_framework.request"}),
    "services/call_service.py": frozenset({"rest_framework.request"}),
    "types/http_extras.py": frozenset({"rest_framework.request"}),
    "types/offline_context.py": frozenset({"rest_framework.request"}),
    "types/offline_service_view.py": frozenset({"rest_framework.request"}),
    "types/selector_spec.py": frozenset(
        {
            "rest_framework.permissions",
            "rest_framework.request",
        }
    ),
    "types/service_spec.py": frozenset(
        {
            "rest_framework.permissions",
            "rest_framework.response",
        }
    ),
    "types/service_view.py": frozenset({"rest_framework.request"}),
}


@cache
def _is_module(dotted: str) -> bool:
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError):
        # ``find_spec`` imports the parent to look inside it; a parent that is
        # itself not a package raises rather than answering ``None``.
        return False


def _imported_modules(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                yield candidate if _is_module(candidate) else node.module


def _is_outside_transport(relative: Path) -> bool:
    if relative.as_posix() in EXEMPT_MODULES:
        return False
    return relative.parts[0] not in TRANSPORT_PACKAGES


def _imports_matching(prefixes: tuple[str, ...]) -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT)
        if not _is_outside_transport(relative):
            continue
        targets = frozenset(
            module
            for module in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)
        )
        if targets:
            found[relative.as_posix()] = targets
    return found


def _difference(
    left: dict[str, frozenset[str]], right: dict[str, frozenset[str]]
) -> dict[str, list[str]]:
    return {
        module: sorted(targets - right.get(module, frozenset()))
        for module, targets in left.items()
        if targets - right.get(module, frozenset())
    }


def test_no_new_import_of_the_http_views_outside_the_transport() -> None:
    added = _difference(_imports_matching(VIEWS_PREFIXES), ALLOWED_VIEWS_IMPORTS)
    assert not added, (
        f"New imports of the HTTP views from outside the transport: {added}. Move what "
        "the module needs out of views/ into the layer that owns it, or, if the "
        "module is itself HTTP-only, file it under a transport package. Adding it to "
        "ALLOWED_VIEWS_IMPORTS is a decision to widen the coupling; say why beside it."
    )


def test_every_allowed_views_import_still_exists() -> None:
    gone = _difference(ALLOWED_VIEWS_IMPORTS, _imports_matching(VIEWS_PREFIXES))
    assert not gone, (
        f"These allowed imports of the HTTP views no longer exist: {gone}. Remove them "
        "from ALLOWED_VIEWS_IMPORTS, so the list keeps recording what is left."
    )


def test_no_new_import_of_drf_outside_the_transport() -> None:
    added = _difference(_imports_matching(DRF_PREFIXES), ALLOWED_DRF_IMPORTS)
    assert not added, (
        f"New imports of DRF from outside the transport: {added}. The dispatch core "
        "runs with no DRF object constructed; a new dependency on DRF there is a "
        "step away from that. Adding it to ALLOWED_DRF_IMPORTS is a decision to "
        "widen the coupling; say why beside it."
    )


def test_every_allowed_drf_import_still_exists() -> None:
    gone = _difference(ALLOWED_DRF_IMPORTS, _imports_matching(DRF_PREFIXES))
    assert not gone, (
        f"These allowed imports of DRF no longer exist: {gone}. Remove them from "
        "ALLOWED_DRF_IMPORTS, so the list keeps recording what is left."
    )
