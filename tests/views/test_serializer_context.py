"""End-to-end coverage for serializer-context propagation.

Verifies that input/output serializers built by the library see DRF's
default context (``request``, ``view``, ``format``), that the directional
hooks (``get_input_serializer_context`` / ``get_output_serializer_context``)
override on top, and that per-action hooks
(``get_<action>_input_serializer_context`` etc.) win over the directional
ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _AuthorIn:
    name: str


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _update_author(*, instance: Author, data: _AuthorIn) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


def _create_author_from_dict(*, data: dict[str, Any]) -> Author:
    return Author.objects.create(name=data["name"])


def _update_author_from_dict(*, instance: Author, data: dict[str, Any]) -> Author:
    instance.name = data["name"]
    instance.save(update_fields=["name"])
    return instance


class _ContextCapturingInputSerializer(serializers.Serializer):
    """Serializer that records the ``context`` dict it was instantiated with."""

    name = serializers.CharField()
    captured: dict[str, Any] = {}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        type(self).captured = dict(self.context)
        return attrs


class _ContextCapturingOutputSerializer(serializers.ModelSerializer):
    captured: dict[str, Any] = {}

    class Meta:
        model = Author
        fields = ("id", "name")

    def to_representation(self, instance: Any) -> Any:
        type(self).captured = dict(self.context)
        return super().to_representation(instance)


def _reset_capture() -> None:
    _ContextCapturingInputSerializer.captured = {}
    _ContextCapturingOutputSerializer.captured = {}


@pytest.mark.django_db
class TestInputSerializerContext:
    def test_input_serializer_receives_drf_default_context(self) -> None:
        _reset_capture()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=lambda **_: {"ok": True},
                input_serializer=_ContextCapturingInputSerializer,
                atomic=False,
            )

        request = factory.post("/", {"name": "Ada"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        ctx = _ContextCapturingInputSerializer.captured
        assert "request" in ctx
        assert "view" in ctx
        assert "format" in ctx
        assert isinstance(ctx["view"], _View)

    def test_get_serializer_context_flows_into_input(self) -> None:
        _reset_capture()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=lambda **_: {"ok": True},
                input_serializer=_ContextCapturingInputSerializer,
                atomic=False,
            )

            def get_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_serializer_context()
                ctx["tenant"] = "acme"
                return ctx

        request = factory.post("/", {"name": "Ada"}, format="json")
        _View.as_view()(request)
        assert _ContextCapturingInputSerializer.captured["tenant"] == "acme"

    def test_directional_input_hook_overrides_drf(self) -> None:
        _reset_capture()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=lambda **_: {"ok": True},
                input_serializer=_ContextCapturingInputSerializer,
                atomic=False,
            )

            def get_input_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_input_serializer_context()
                ctx["origin"] = "input-only"
                return ctx

        request = factory.post("/", {"name": "Ada"}, format="json")
        _View.as_view()(request)
        assert _ContextCapturingInputSerializer.captured["origin"] == "input-only"

    def test_directional_input_hook_does_not_leak_into_output(self) -> None:
        _reset_capture()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=lambda **_: {"id": 1, "name": "x"},
                input_serializer=_ContextCapturingInputSerializer,
                output_serializer=_ContextCapturingOutputSerializer,
                atomic=False,
            )

            def get_input_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_input_serializer_context()
                ctx["input_only"] = True
                return ctx

        Author.objects.create(name="x")  # so the lambda's id=1 maps to a real author
        request = factory.post("/", {"name": "x"}, format="json")
        _View.as_view()(request)
        assert _ContextCapturingInputSerializer.captured.get("input_only") is True
        assert "input_only" not in _ContextCapturingOutputSerializer.captured


@pytest.mark.django_db
class TestOutputSerializerContext:
    def test_output_serializer_receives_drf_default_context(self) -> None:
        _reset_capture()
        author = Author.objects.create(name="seed")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: author),
                output_serializer=_ContextCapturingOutputSerializer,
                atomic=False,
            )

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        ctx = _ContextCapturingOutputSerializer.captured
        assert "request" in ctx
        assert "view" in ctx
        assert "format" in ctx

    def test_directional_output_hook_overrides_drf(self) -> None:
        _reset_capture()
        author = Author.objects.create(name="seed")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: author),
                output_serializer=_ContextCapturingOutputSerializer,
                atomic=False,
            )

            def get_output_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_output_serializer_context()
                ctx["origin"] = "output-only"
                return ctx

        request = factory.post("/", {}, format="json")
        _View.as_view()(request)
        assert _ContextCapturingOutputSerializer.captured["origin"] == "output-only"

    def test_directional_output_hook_does_not_leak_into_input(self) -> None:
        _reset_capture()
        author = Author.objects.create(name="seed")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: author),
                input_serializer=_ContextCapturingInputSerializer,
                output_serializer=_ContextCapturingOutputSerializer,
                atomic=False,
            )

            def get_output_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_output_serializer_context()
                ctx["output_only"] = True
                return ctx

        request = factory.post("/", {"name": "seed"}, format="json")
        _View.as_view()(request)
        assert _ContextCapturingOutputSerializer.captured.get("output_only") is True
        assert "output_only" not in _ContextCapturingInputSerializer.captured


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


def _retrieve_author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


@pytest.mark.django_db
class TestPerActionContextOnViewset:
    def _make_viewset(self) -> type[ServiceViewSet]:
        class _ViewSet(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "create": ServiceSpec(
                    service=_create_author_from_dict,
                    input_serializer=_ContextCapturingInputSerializer,
                    output_serializer=_ContextCapturingOutputSerializer,
                ),
                "update": ServiceSpec(
                    service=_update_author_from_dict,
                    input_serializer=_ContextCapturingInputSerializer,
                    output_serializer=_ContextCapturingOutputSerializer,
                ),
            }

            def get_create_input_serializer_context(self) -> dict[str, Any]:
                return {"per_action": "create-input"}

            def get_update_output_serializer_context(self) -> dict[str, Any]:
                return {"per_action": "update-output"}

        return _ViewSet

    def test_create_input_action_hook_visible_only_on_create(self) -> None:
        _reset_capture()
        view = self._make_viewset().as_view({"post": "create"})
        request = factory.post("/", {"name": "Ada"}, format="json")
        view(request)
        assert _ContextCapturingInputSerializer.captured["per_action"] == "create-input"
        assert "per_action" not in _ContextCapturingOutputSerializer.captured

    def test_update_output_action_hook_visible_only_on_update(self) -> None:
        _reset_capture()
        author = Author.objects.create(name="orig")
        view = self._make_viewset().as_view({"put": "update"})
        request = factory.put("/", {"name": "new"}, format="json")
        view(request, pk=author.pk)
        assert _ContextCapturingOutputSerializer.captured["per_action"] == "update-output"
        assert "per_action" not in _ContextCapturingInputSerializer.captured


@pytest.mark.django_db
class TestStandaloneViewActionIsNone:
    """``self.action`` is ``None`` on standalone views; per-action hooks must
    be skipped, and only the global / directional hooks fire."""

    def test_per_action_hook_inactive_on_standalone_view(self) -> None:
        _reset_capture()
        author = Author.objects.create(name="orig")

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_update_author_from_dict,
                input_serializer=_ContextCapturingInputSerializer,
                output_serializer=_ContextCapturingOutputSerializer,
            )

            def get_update_input_serializer_context(self) -> dict[str, Any]:
                # Defined but never called: standalone view has no `action`.
                return {"per_action": "update-input"}

        request = factory.put("/", {"name": "new"}, format="json")
        _View.as_view()(request, pk=author.pk)
        # The per-action hook should NOT have fired:
        assert "per_action" not in _ContextCapturingInputSerializer.captured
        # Sanity: DRF default context is still there.
        assert "request" in _ContextCapturingInputSerializer.captured


@pytest.mark.django_db
class TestServiceWithoutOutputSerializerDoesNotErrorOnContext:
    """A service that has no output_serializer must still resolve contexts
    cleanly (regression: the context resolution path runs before the
    rendering branch picks no-output-serializer)."""

    def test_no_output_serializer(self) -> None:
        _reset_capture()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: {"raw": True}),
                atomic=False,
            )

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        assert response.data == {"raw": True}


@pytest.mark.django_db
class TestDataclassInputSerializerReceivesContext:
    """When ``input_serializer`` is a bare dataclass, the auto-wrapping
    DataclassSerializer branch must also forward the context."""

    captured_ctx: dict[str, Any] = {}

    def test_dataclass_branch_forwards_context(self) -> None:
        TestDataclassInputSerializerReceivesContext.captured_ctx = {}

        @dataclass
        class _Payload:
            name: str

        captured_ctx_holder = TestDataclassInputSerializerReceivesContext

        def fn(*, data: _Payload) -> dict[str, Any]:
            return {"ok": True}

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn, input_serializer=_Payload, atomic=False)

            def get_input_serializer_context(self) -> dict[str, Any]:
                ctx = super().get_input_serializer_context()
                ctx["marker"] = "dataclass-branch"
                # Stash a reference so we can inspect it after the request.
                captured_ctx_holder.captured_ctx = ctx
                return ctx

        request = factory.post("/", {"name": "Ada"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        # The context dict our hook returned was passed straight through to
        # the DataclassSerializer (so the marker key is what we put in).
        assert TestDataclassInputSerializerReceivesContext.captured_ctx["marker"] == (
            "dataclass-branch"
        )


# --- selector_action context propagation ---------------------------------


def _capturing_serializer_factory() -> type[serializers.ModelSerializer]:
    """Fresh subclass per test so the ``captured`` class var is isolated."""

    class _Capturing(serializers.ModelSerializer):
        captured: dict[str, Any] = {}

        class Meta:
            model = Author
            fields = ("id", "name")

        def to_representation(self, instance: Any) -> Any:
            type(self).captured = dict(self.context)
            return super().to_representation(instance)

    return _Capturing


@pytest.mark.django_db
class TestSelectorActionContext:
    """``@selector_action`` with a spec-level output_serializer should also
    receive DRF default context plus any per-action override."""

    def test_collection_action_default_context(self) -> None:
        from rest_framework.viewsets import GenericViewSet

        from rest_framework_services import SelectorSpec, selector_action

        Author.objects.create(name="x")
        capturing = _capturing_serializer_factory()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    selector=lambda **_: list(Author.objects.all()),
                    output_serializer=capturing,
                ),
                detail=False,
            )
            def active(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        request = factory.get("/")
        _View.as_view({"get": "active"})(request)
        ctx = capturing.captured
        assert "request" in ctx
        assert "view" in ctx
        assert "format" in ctx

    def test_action_specific_output_hook(self) -> None:
        from rest_framework.viewsets import GenericViewSet

        from rest_framework_services import SelectorSpec, selector_action

        Author.objects.create(name="x")
        capturing = _capturing_serializer_factory()

        class _View(GenericViewSet):
            serializer_class = AuthorSerializer
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    selector=lambda **_: list(Author.objects.all()),
                    output_serializer=capturing,
                ),
                detail=False,
            )
            def active(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

            def get_active_output_serializer_context(self) -> dict[str, Any]:
                return {"per_action": "active"}

        request = factory.get("/")
        _View.as_view({"get": "active"})(request)
        assert capturing.captured["per_action"] == "active"
