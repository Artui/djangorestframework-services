"""``OfflineHttpRequest`` — the synthetic ``HttpRequest`` used off the HTTP path."""

from __future__ import annotations

from django.http import HttpRequest


class OfflineHttpRequest(HttpRequest):
    """An :class:`~django.http.HttpRequest` with no ambient host, made safe to use.

    Built by :func:`~rest_framework_services.build_offline_context` when the
    caller has no real request to wrap (a Pydantic-AI toolset, a management
    command, a task runner). A bare ``HttpRequest`` has an **empty** ``META``,
    and Django resolves the host from ``HTTP_HOST`` / ``SERVER_NAME`` — so
    ``get_host()`` raises ``KeyError: 'SERVER_NAME'`` and takes
    ``build_absolute_uri()`` down with it. That reaches serializers through the
    most ordinary field there is: DRF's ``FileField.to_representation`` calls
    ``request.build_absolute_uri(value.url)`` whenever a ``request`` is in the
    context.

    Two behaviours, decided by whether the caller configured a host
    (``build_offline_context(host=…)``, which seeds ``META`` — a configured
    request is an ordinary one and none of this applies):

    - **Host configured** — nothing here intervenes. Absolute URIs are built
      by Django exactly as on HTTP.
    - **No host** — ``build_absolute_uri`` returns the location unchanged, i.e.
      the *relative* URL. There is no honest absolute URL to return: the process
      has no idea what origin serves it, and a guess (the first
      ``ALLOWED_HOSTS`` entry, say) would emit confidently-wrong links that look
      valid. A relative URL is the same thing DRF's own file / hyperlinked
      fields fall back to when there is no request in the context at all, so
      this is the shape those serializers already handle.

    ``get_host()`` still raises without a host — but with a message naming the
    fix, rather than a bare ``KeyError`` from Django's internals.
    """

    #: The configured host (``"example.com"`` / ``"example.com:8000"``), or
    #: ``None`` when the caller didn't configure one. Set by
    #: :func:`~rest_framework_services.build_offline_context`; when it is set,
    #: ``META`` carries the matching keys and Django's own machinery is in
    #: charge, so nothing on this class changes behaviour.
    offline_host: str | None = None

    def build_absolute_uri(self, location: str | None = None) -> str:
        """Absolute URI when a host is configured; the location itself when not.

        Degrading beats raising: the caller is a serializer field rendering a
        link, and a relative URL is a usable answer that its consumer can
        resolve against whatever origin it reached the data through.
        """
        if self.offline_host is not None:
            return super().build_absolute_uri(location)
        if location is None:
            return self.get_full_path()
        return location

    def _get_scheme(self) -> str:
        """Read the scheme from ``META``, as ``WSGIRequest`` does.

        Django's documented override point: the base ``HttpRequest`` hard-codes
        ``"http"`` and only the WSGI/ASGI subclasses consult
        ``wsgi.url_scheme``, so without this a ``host="https://…"`` would still
        build ``http://`` links.
        """
        return str(self.META.get("wsgi.url_scheme", "http"))

    def get_host(self) -> str:
        """The configured host verbatim; a pointed error when there is none.

        Deliberately **not** validated against ``ALLOWED_HOSTS``. That setting
        rejects spoofed ``Host`` headers from untrusted clients; this value came
        from the project's own code, and there is no client. Requiring it to
        appear in ``ALLOWED_HOSTS`` would break the ordinary case of a worker or
        management command that renders links for a site it does not itself
        serve — and would add nothing, since a caller who can pass a host can
        equally pass one that is on the list.
        """
        if self.offline_host is not None:
            return self.offline_host
        raise ValueError(
            "This request was synthesized off the HTTP path and has no host. "
            "Pass build_offline_context(host='example.com') (or "
            "host='https://example.com') to give it one — e.g. from your Sites "
            "framework entry, or a project setting naming your public origin. "
            "Without a host, build_absolute_uri() returns relative URLs instead "
            "of raising."
        )


__all__ = ["OfflineHttpRequest"]
