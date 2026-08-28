"""Transport-neutral spec dispatch.

The single execution path an alternate transport (the MCP server, a CLI, a
task runner) drives instead of re-implementing validate → resolve → run →
shape → materialize. The HTTP view layer keeps its own request-coupled
orchestration; everything off the HTTP path composes these.
"""

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.arender_for_agent import arender_for_agent
from rest_framework_services.dispatch.arender_spec_output import arender_spec_output
from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.dispatch.enforce_permissions import enforce_permissions

# Must stay *after* ``base_pool``. Importing that module imports this one as
# a side effect, and Python then binds the **submodule** onto the package —
# overwriting a from-import placed earlier, so ``null_progress`` would resolve
# to a module object rather than the function. Alphabetical order happens to be
# correct here; if these are ever reordered, keep this one last of the two.
from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.dispatch.paginate_for_agent import (
    DEFAULT_AGENT_PAGE_SIZE,
    paginate_for_agent,
)
from rest_framework_services.dispatch.render_for_agent import render_for_agent
from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.dispatch.unguarded_specs import unguarded_specs

__all__ = [
    "adispatch_spec",
    "arender_for_agent",
    "arender_spec_output",
    "base_pool",
    "base_serializer_context",
    "build_offline_context",
    "combine_progress",
    "dispatch_spec",
    "enforce_permissions",
    "DEFAULT_AGENT_PAGE_SIZE",
    "null_progress",
    "paginate_for_agent",
    "render_for_agent",
    "render_spec_output",
    "unguarded_specs",
]
