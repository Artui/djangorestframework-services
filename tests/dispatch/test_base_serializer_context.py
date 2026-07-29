"""``base_serializer_context`` — DRF's baseline context, on and off the HTTP path."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    base_serializer_context,
    build_offline_context,
    render_spec_output,
)
from tests.testapp.models import Post


class _ViewWithContext:
    """Stands in for a DRF ``GenericAPIView`` (which owns the hook)."""

    def get_serializer_context(self) -> dict[str, Any]:
        return {"request": "HTTP-REQ", "format": "json", "view": self, "extra": 1}


class TestBaseSerializerContext:
    def test_synthesizes_drf_shape_without_a_view_hook(self) -> None:
        view = object()
        assert base_serializer_context(view=view, request="REQ") == {
            "request": "REQ",
            "format": None,
            "view": view,
        }

    def test_keys_present_even_when_nothing_was_passed(self) -> None:
        # The point of the baseline: ``self.context["request"]`` never KeyErrors.
        assert base_serializer_context(view=None, request=None) == {
            "request": None,
            "format": None,
            "view": None,
        }

    def test_view_hook_wins_when_present(self) -> None:
        view = _ViewWithContext()
        assert base_serializer_context(view=view, request="IGNORED") == {
            "request": "HTTP-REQ",
            "format": "json",
            "view": view,
            "extra": 1,
        }

    def test_result_is_a_fresh_dict_the_caller_may_mutate(self) -> None:
        view = _ViewWithContext()
        context = base_serializer_context(view=view, request=None)
        context["request"] = "OVERRIDDEN"
        assert view.get_serializer_context()["request"] == "HTTP-REQ"

    def test_non_callable_attribute_is_not_mistaken_for_the_hook(self) -> None:
        class _Odd:
            get_serializer_context = "not a method"

        view = _Odd()
        assert base_serializer_context(view=view, request="REQ")["request"] == "REQ"

    def test_offline_context_pair_carries_the_synthetic_request(self) -> None:
        offline = build_offline_context(user="U")
        context = base_serializer_context(view=offline.view, request=offline.request)
        assert context["request"] is offline.request
        assert context["request"].user == "U"
        assert context["view"] is offline.view
        assert context["format"] is None


class _FileFieldShapedSerializer(serializers.Serializer):
    """Mirrors DRF's ``FileField.to_representation`` branch for a URL."""

    url = serializers.SerializerMethodField()

    def get_url(self, _: object) -> str:
        request = self.context.get("request", None)
        if request is not None:
            return request.build_absolute_uri("/media/doc.pdf")
        return "/media/doc.pdf"


@pytest.mark.django_db
class TestFileFieldsOffHttp:
    """Regression: a request in the context must not be worse than none.

    0.29.0 started supplying ``request`` off HTTP, which is what makes
    ``request.user`` work — but the synthesized request had an empty ``META``,
    so ``build_absolute_uri`` raised ``KeyError: 'SERVER_NAME'``. Any serializer
    with a ``FileField`` broke on a path that previously returned a relative URL.
    """

    def _render(self, host: str | None) -> Any:
        Post.objects.create(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Post.objects.all(),
            output_serializer=_FileFieldShapedSerializer,
        )
        offline = build_offline_context(user=None, host=host)
        return render_spec_output(
            spec,
            list(Post.objects.all()),
            many=True,
            request=offline.request,
            view=offline.view,
        )

    def test_without_a_host_the_url_is_relative(self) -> None:
        assert [row["url"] for row in self._render(None)] == ["/media/doc.pdf"]

    def test_with_a_host_the_url_is_absolute(self) -> None:
        assert [row["url"] for row in self._render("https://files.example.com")] == [
            "https://files.example.com/media/doc.pdf"
        ]
