"""``base_serializer_context`` — DRF's baseline context, on and off the HTTP path."""

from __future__ import annotations

from typing import Any

from rest_framework_services import base_serializer_context, build_offline_context


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
