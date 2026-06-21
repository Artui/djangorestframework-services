"""Transport-neutral spec dispatch.

The single execution path an alternate transport (the MCP server, a CLI, a
task runner) drives instead of re-implementing validate → resolve → run →
shape → materialize. The HTTP view layer keeps its own request-coupled
orchestration; everything off the HTTP path composes these.
"""

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.dispatch.enforce_permissions import enforce_permissions
from rest_framework_services.dispatch.render_spec_output import render_spec_output

__all__ = [
    "adispatch_spec",
    "build_offline_context",
    "dispatch_spec",
    "enforce_permissions",
    "render_spec_output",
]
