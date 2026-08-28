"""``AgentContract`` -- what a transport with no HTTP request has to be told."""

from __future__ import annotations

from dataclasses import dataclass, field

from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.url_kwarg import UrlKwarg


@dataclass(frozen=True)
class AgentContract:
    """The declarations an off-HTTP caller needs and an HTTP one never does.

    **Over HTTP this is all free.** A nested route's captures reach a spec
    through ``view.kwargs`` because the URLconf declared them; read-shaping
    params reach a serializer through ``request.query_params`` because the query
    string carried them. A spec mounted on a view is already complete.

    Off HTTP there is no route and no query string, so somebody has to say what
    they would have contained. That is what this is: **not a missing part of the
    operation, but a description of the request that is not there.**

    ``UrlKwarg("project_pk")`` means *"when there is no URL, synthesise this view
    kwarg from a caller-supplied argument"* -- a sentence with no meaning on a
    transport that has a URL. Which is exactly why it does not belong on the spec
    itself: the spec would carry a field HTTP must ignore, and a second
    declaration of a fact the URLconf already owns, pointing the other way.

    **It belongs to the entry rather than to any one transport** because every
    off-HTTP transport needs the *identical* answer. MCP and an in-process
    Pydantic-AI toolset synthesise the same absent request for the same
    operation; a project running both used to declare it twice, in two shapes,
    with nothing comparing them.

    Deliberately **not** a home for bounds or strictness -- result-size caps,
    page ceilings, timeouts, unknown-argument policy. Those legitimately differ
    between a publicly exposed server and an in-process toolset, and one shared
    number would be a regression rather than a simplification. This carries only
    what cannot differ.

    Sorting is absent for the same reason from the other direction: it is
    declared by the spec's own ``filter_set``, which is already read by every
    transport, so it needs no second home.

    Transports read this as a **default**. A mount may still override it, and one
    that says nothing inherits it.
    """

    url_kwargs: tuple[UrlKwarg, ...] = field(default_factory=tuple)
    query_params: tuple[QueryParam, ...] = field(default_factory=tuple)


__all__ = ["AgentContract"]
