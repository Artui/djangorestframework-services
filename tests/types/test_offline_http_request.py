"""``OfflineHttpRequest`` — absolute-URI building with and without a host."""

from __future__ import annotations

import pytest

from rest_framework_services import OfflineHttpRequest, build_offline_context


class TestWithoutAHost:
    def test_build_absolute_uri_returns_the_location_unchanged(self) -> None:
        # The regression this guards: a bare HttpRequest has an empty META, so
        # Django's host lookup raises ``KeyError: 'SERVER_NAME'`` here.
        assert OfflineHttpRequest().build_absolute_uri("/media/doc.pdf") == "/media/doc.pdf"

    def test_build_absolute_uri_without_a_location_is_the_full_path(self) -> None:
        request = OfflineHttpRequest()
        assert request.build_absolute_uri() == request.get_full_path()

    def test_get_host_raises_something_actionable(self) -> None:
        with pytest.raises(ValueError, match="has no host"):
            OfflineHttpRequest().get_host()


class TestWithAHost:
    """A configured host is the project's own, so no ``ALLOWED_HOSTS`` check.

    These hosts are absent from the test settings' ``ALLOWED_HOSTS`` on purpose:
    a worker that renders links for a site it doesn't serve must still work.
    """

    def test_bare_hostname_defaults_to_http(self) -> None:
        request = build_offline_context(user=None, host="example.com").request
        assert request.build_absolute_uri("/media/doc.pdf") == "http://example.com/media/doc.pdf"
        assert request.get_host() == "example.com"

    def test_full_origin_carries_its_scheme(self) -> None:
        request = build_offline_context(user=None, host="https://example.com").request
        assert request.build_absolute_uri("/x/") == "https://example.com/x/"
        assert request.scheme == "https"

    def test_explicit_port_is_preserved(self) -> None:
        request = build_offline_context(user=None, host="example.com:8000").request
        assert request.build_absolute_uri("/x/") == "http://example.com:8000/x/"

    def test_full_origin_with_port(self) -> None:
        request = build_offline_context(user=None, host="https://example.com:8443").request
        assert request.build_absolute_uri("/x/") == "https://example.com:8443/x/"

    def test_trailing_slash_on_a_bare_host_is_tolerated(self) -> None:
        request = build_offline_context(user=None, host="example.com/").request
        assert request.build_absolute_uri("/x/") == "http://example.com/x/"

    def test_an_absolute_location_passes_through(self) -> None:
        request = build_offline_context(user=None, host="example.com").request
        assert (
            request.build_absolute_uri("https://cdn.example.net/f") == "https://cdn.example.net/f"
        )


class TestHostAndAmbientRequest:
    def test_a_real_request_keeps_its_own_host(self) -> None:
        # ``host`` configures the *synthesized* request only, so a caller can
        # pass both unconditionally: the ambient request when there is one.
        from django.test import RequestFactory

        http_request = RequestFactory().post("/hook/")
        request = build_offline_context(
            user=None, http_request=http_request, host="ignored.example.com"
        ).request
        # ``testserver`` is RequestFactory's own host — the real request's, not ours.
        assert request.build_absolute_uri("/x/") == "http://testserver/x/"

    def test_no_host_still_yields_a_usable_request(self) -> None:
        context = build_offline_context(user="alice")
        assert context.request.user == "alice"
        assert context.request.build_absolute_uri("/media/doc.pdf") == "/media/doc.pdf"
