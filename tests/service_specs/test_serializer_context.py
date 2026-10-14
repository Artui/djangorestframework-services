"""The per-spec serializer context callables, carried onto the compiled spec's adapters.

A drfs spec's ``input_serializer_context`` and ``output_serializer_context`` are
the fourth layer of the serializer context: merged over DRF's own context (the
first), and over the view hooks the DRF edge registers (the second and third),
with the final say on any key they share. These tests dispatch the compiled
spec through the kernel directly, with no edge and so none of the view-hook
layers, which is how a transport speaking the kernel's language reaches it.
"""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.adispatch import adispatch
from django_service_specs.dispatch.apresent import apresent
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.dispatch_result import DispatchResult
from django_service_specs.dispatch.present import present
from django_service_specs.output.presentation_context import PresentationContext
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework import serializers

from rest_framework_services.service_specs.as_kernel_spec import as_kernel_spec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unset import UNSET
from tests.testapp.models import Post


def _capturing(seen: list[dict[str, Any]]) -> type[serializers.Serializer]:
    """An input serializer recording the context it validated under."""

    class _In(serializers.Serializer):
        title = serializers.CharField()

        def validate(self, attrs: Any) -> Any:
            seen.append(dict(self.context))
            return attrs

    return _In


class _Out(serializers.Serializer):
    title = serializers.CharField()
    context = serializers.SerializerMethodField()

    def get_context(self, obj: Any) -> dict[str, Any]:
        return {key: self.context.get(key) for key in ("tenant", "shared", "format", "who")}


def _create(*, data: Any) -> SimpleNamespace:
    return SimpleNamespace(title=data["title"])


def _fixed() -> SimpleNamespace:
    return SimpleNamespace(title="hi")


def _service(**fields: Any) -> ServiceSpec[Any, Any, Any]:
    output = fields.pop("output", None)
    return ServiceSpec(
        service=fields.pop("service", _create if "input_serializer" in fields else _fixed),
        permission_classes=[],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
            output_serializer_context=output,
        ),
        **fields,
    )


class _View:
    """A registered view answering ``get_serializer_context``, as DRF's views do."""

    kwargs: dict[str, Any] = {}

    def __init__(self, request: Any = None) -> None:
        self.request = request

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "format": None,
            "view": self,
            "base": "view",
            "shared": "view",
        }


# --- the input serializer --------------------------------------------------------


@pytest.mark.django_db
def test_the_input_callable_reaches_the_input_serializer() -> None:
    seen: list[dict[str, Any]] = []
    spec = _service(
        input_serializer=_capturing(seen),
        input_serializer_context=lambda: {"tenant": "acme"},
    )

    dispatch(spec, principal=None, arguments={"title": "hi"})

    assert seen[0]["tenant"] == "acme"
    # Beneath it, DRF's own three keys are still there.
    assert {"request", "format", "view"} <= set(seen[0])


@pytest.mark.django_db
def test_the_input_callable_wins_over_the_view_s_own_context_on_a_shared_key() -> None:
    seen: list[dict[str, Any]] = []
    view = _View()
    spec = _service(
        input_serializer=_capturing(seen),
        input_serializer_context=lambda: {"shared": "spec"},
    )

    dispatch(
        spec,
        principal=None,
        arguments={"title": "hi"},
        pool_seeds=DEFAULT_POOL_SEEDS.extend(view=lambda: view),
    )

    assert seen[0]["shared"] == "spec"
    # The registered view still supplies the first layer.
    assert seen[0]["base"] == "view"


@pytest.mark.django_db
def test_the_input_callable_declares_request_view_and_user() -> None:
    """With no seed registered, the callable is handed the offline pair the serializer gets."""
    principal = get_user_model().objects.create(username="ada")
    seen: list[dict[str, Any]] = []
    received: dict[str, Any] = {}

    def provider(request: Any, view: Any, user: Any) -> dict[str, Any]:
        received.update(request=request, view=view, user=user)
        return {}

    spec = _service(input_serializer=_capturing(seen), input_serializer_context=provider)

    dispatch(spec, principal=principal, arguments={"title": "hi"})

    assert received["user"] is principal
    assert received["request"] is seen[0]["request"]
    assert received["view"] is seen[0]["view"]
    assert received["request"].user is principal


@pytest.mark.django_db
def test_an_argument_never_stands_in_for_the_request_the_callable_declares() -> None:
    """An argument is the lowest layer of the callable's pool, beneath the call's own request."""
    seen: list[dict[str, Any]] = []
    received: dict[str, Any] = {}

    class _WithRequest(serializers.Serializer):
        title = serializers.CharField()
        request = serializers.CharField()

        def validate(self, attrs: Any) -> Any:
            seen.append(dict(self.context))
            return attrs

    def provider(request: Any, title: str) -> dict[str, Any]:
        received.update(request=request, title=title)
        return {}

    spec = _service(input_serializer=_WithRequest, input_serializer_context=provider)

    dispatch(spec, principal=None, arguments={"title": "hi", "request": "mallory"})

    assert received == {"request": seen[0]["request"], "title": "hi"}


@pytest.mark.django_db
def test_an_unset_value_declines_the_key_and_keeps_the_layer_beneath() -> None:
    seen: list[dict[str, Any]] = []
    spec = _service(
        input_serializer=_capturing(seen),
        input_serializer_context=lambda: {"format": UNSET, "tenant": "acme"},
    )

    dispatch(spec, principal=None, arguments={"title": "hi"})

    assert seen[0]["format"] is None
    assert seen[0]["tenant"] == "acme"


@pytest.mark.django_db
def test_no_input_callable_leaves_the_view_s_context_as_it_is() -> None:
    seen: list[dict[str, Any]] = []
    view = _View()
    spec = _service(input_serializer=_capturing(seen))

    dispatch(
        spec,
        principal=None,
        arguments={"title": "hi"},
        pool_seeds=DEFAULT_POOL_SEEDS.extend(view=lambda: view),
    )

    assert seen[0]["shared"] == "view"


@pytest.mark.django_db
def test_a_many_spec_s_rows_validate_under_the_input_callable_once() -> None:
    seen: list[dict[str, Any]] = []
    calls: list[None] = []

    def provider() -> dict[str, Any]:
        calls.append(None)
        return {"tenant": "acme"}

    spec = ServiceSpec(
        service=lambda *, data: data,
        permission_classes=[],
        many=True,
        input_serializer=_capturing(seen),
        input_serializer_context=provider,
    )

    dispatch(spec, principal=None, arguments={"items": [{"title": "a"}, {"title": "b"}]})

    assert len(calls) == 1
    assert [context["tenant"] for context in seen] == ["acme", "acme"]


@pytest.mark.django_db
def test_a_registered_seed_reaches_the_input_callable() -> None:
    seen: list[dict[str, Any]] = []
    spec = _service(
        input_serializer=_capturing(seen),
        input_serializer_context=lambda tenant: {"tenant": tenant},
    )

    dispatch(
        spec,
        principal=None,
        arguments={"title": "hi"},
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )

    assert seen[0]["tenant"] == "acme"


@pytest.mark.django_db(transaction=True)
async def test_an_input_callable_that_queries_runs_off_the_event_loop() -> None:
    seen: list[dict[str, Any]] = []
    spec = _service(
        input_serializer=_capturing(seen),
        input_serializer_context=lambda: {"tenant": Post.objects.count()},
    )

    await adispatch(spec, principal=AnonymousUser(), arguments={"title": "hi"})

    assert seen[0]["tenant"] == 0


# --- the output serializer -------------------------------------------------------


@pytest.mark.django_db
def test_the_output_callable_reaches_the_output_serializer() -> None:
    spec = _service(output=lambda: {"tenant": "acme"})

    result = dispatch(spec, principal=None, arguments={})

    assert present(spec, result)["context"]["tenant"] == "acme"


def test_the_output_callable_declares_result_and_user() -> None:
    principal = SimpleNamespace(is_authenticated=True, is_active=True, pk=1)
    received: dict[str, Any] = {}

    def provider(result: Any, user: Any) -> dict[str, Any]:
        received.update(result=result, user=user)
        return {"who": result.title}

    spec = _service(output=provider)
    value = SimpleNamespace(title="hi")
    context = PresentationContext(
        principal=principal, ambient=MappingProxyType({"user": principal})
    )

    payload = present(spec, DispatchResult(kind="instance", value=value, context=context))

    assert received == {"result": value, "user": principal}
    assert payload["context"]["who"] == "hi"


def test_the_output_callable_wins_over_the_view_s_own_context_on_a_shared_key() -> None:
    view = _View()
    spec = _service(output=lambda: {"shared": "spec"})
    context = PresentationContext(principal=None, ambient=MappingProxyType({"view": view}))

    payload = present(
        spec, DispatchResult(kind="instance", value=SimpleNamespace(title="t"), context=context)
    )

    assert payload["context"]["shared"] == "spec"


@pytest.mark.django_db
def test_a_retrieve_selector_s_output_callable_declares_instance() -> None:
    row = SimpleNamespace(title="hi")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: row,
        permission_classes=[],
        output_serializer=_Out,
        output_serializer_context=lambda instance: {"tenant": instance.title},
    )

    result = dispatch(spec, principal=None, arguments={})

    assert present(spec, result)["context"]["tenant"] == "hi"


@pytest.mark.django_db
def test_a_list_s_output_callable_is_asked_per_row_and_offered_no_row_by_name() -> None:
    """The kernel presents a list row by row, so the whole ``page`` is never in hand."""
    received: list[dict[str, Any]] = []

    def provider(**pool: Any) -> dict[str, Any]:
        received.append(pool)
        return {"tenant": "acme"}

    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: [SimpleNamespace(title="a"), SimpleNamespace(title="b")],
        permission_classes=[],
        output_serializer=_Out,
        output_serializer_context=provider,
    )

    payload = present(spec, dispatch(spec, principal=None, arguments={}))

    assert [row["context"]["tenant"] for row in payload] == ["acme", "acme"]
    assert len(received) == 2
    assert not {"page", "instance", "result"} & set(received[0])


@pytest.mark.django_db
def test_a_many_service_s_output_callable_is_offered_no_row_by_name() -> None:
    """A ``many`` output's ``result`` is the whole list on drfs' own render; one row is never it."""
    received: list[dict[str, Any]] = []

    def provider(**pool: Any) -> dict[str, Any]:
        received.append(pool)
        return {"tenant": "acme"}

    spec = ServiceSpec(
        service=lambda *, data: [SimpleNamespace(**row) for row in data],
        permission_classes=[],
        many=True,
        input_serializer=_capturing([]),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
            output_serializer_context=provider,
        ),
    )

    result = dispatch(spec, principal=None, arguments={"items": [{"title": "a"}, {"title": "b"}]})
    payload = present(spec, result)

    assert [row["context"]["tenant"] for row in payload] == ["acme", "acme"]
    assert not {"result", "instance", "page"} & set(received[0])


def test_present_with_no_call_runs_no_callable() -> None:
    """``present(value)`` has no call to resolve a callable against, so it renders DRF's context."""
    calls: list[None] = []
    compiled = as_kernel_spec(_service(output=lambda: calls.append(None) or {"tenant": "x"}))
    presenter = compiled.presenter_for_output()
    assert presenter is not None

    payload = presenter.present(SimpleNamespace(title="t"))

    assert calls == []
    assert payload["context"]["tenant"] is None


@pytest.mark.django_db(transaction=True)
async def test_an_output_callable_that_queries_runs_off_the_event_loop() -> None:
    spec = _service(output=lambda: {"tenant": Post.objects.count()})
    result = await adispatch(spec, principal=AnonymousUser(), arguments={})

    payload = await apresent(spec, result)

    assert payload["context"]["tenant"] == 0
