"""The serializer context's four layers, through ``dispatch_spec``.

DRF's own context (the view's ``get_serializer_context``), the directional and
per-action view hooks (resolved by the view into ``ViewHooks``), then the spec's
own callable, each later layer winning on a key it shares with an earlier one.
The edge registers the view-hook layers as a seed of the call, so they reach the
input serializer the kernel validates through and the output serializer the
kernel presents through, and nowhere else.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django_service_specs.dispatch.present import present
from rest_framework import serializers

from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.service_specs.build_offline_context import build_offline_context
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.view_hooks import ViewHooks

_KEYS = ("base", "shared", "hook_in", "hook_out", "spec_in", "spec_out")


class _View:
    """A DRF-like view: its ``get_serializer_context`` is the first layer."""

    def __init__(self, request: Any) -> None:
        self.request = request
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    def get_serializer_context(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "request": self.request,
            "format": None,
            "view": self,
            "base": "view",
            "shared": "view",
        }


class _Out(serializers.Serializer):
    title = serializers.CharField()
    context = serializers.SerializerMethodField()

    def get_context(self, obj: Any) -> dict[str, Any]:
        return {key: self.context[key] for key in _KEYS if key in self.context}


def _create(*, data: Any) -> SimpleNamespace:
    return SimpleNamespace(title=data["title"])


def _spec(
    seen: list[dict[str, Any]],
    *,
    input_context: Any = None,
    output_context: Any = None,
) -> ServiceSpec[Any, Any, Any]:
    class _In(serializers.Serializer):
        title = serializers.CharField()

        def validate(self, attrs: Any) -> Any:
            seen.append({key: self.context[key] for key in _KEYS if key in self.context})
            return attrs

    return ServiceSpec(
        service=_create,
        input_serializer=_In,
        input_serializer_context=input_context,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
            output_serializer_context=output_context,
        ),
    )


def _hooks() -> ViewHooks:
    return ViewHooks(
        input_serializer_context={"shared": "hook", "hook_in": "in"},
        output_serializer_context=lambda result: {"shared": "hook", "hook_out": result.title},
    )


def _call(spec: ServiceSpec[Any, Any, Any], *, hooks: ViewHooks | None = None) -> Any:
    view = _View(build_offline_context(None).request)
    result = dispatch_spec(
        spec,
        user=None,
        params={"title": "hi"},
        request=view.request,
        view=view,
        view_hooks=hooks,
    )
    return result, view


@pytest.mark.django_db
class TestTheInputSerializer:
    def test_the_view_hooks_layer_over_the_view_s_own_context(self) -> None:
        seen: list[dict[str, Any]] = []

        _call(_spec(seen), hooks=_hooks())

        assert seen == [{"base": "view", "shared": "hook", "hook_in": "in"}]

    def test_the_spec_s_callable_has_the_final_say(self) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(seen, input_context=lambda: {"shared": "spec", "spec_in": 1})

        _call(spec, hooks=_hooks())

        assert seen == [{"base": "view", "shared": "spec", "hook_in": "in", "spec_in": 1}]

    def test_the_spec_s_callable_layers_over_the_view_with_no_hooks(self) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(seen, input_context=lambda: {"shared": "spec"})

        _call(spec)

        assert seen == [{"base": "view", "shared": "spec"}]

    def test_each_layer_is_asked_once_per_call(self) -> None:
        seen: list[dict[str, Any]] = []
        asked: list[None] = []

        def provider(request: Any, view: Any) -> dict[str, Any]:
            asked.append(None)
            return {"spec_in": view.request is request}

        _, view = _call(_spec(seen, input_context=provider), hooks=_hooks())

        assert asked == [None]
        assert view.calls == 1
        assert seen[0]["spec_in"] is True


@pytest.mark.django_db
class TestTheOutputSerializer:
    def test_the_view_hooks_layer_over_the_view_s_own_context_with_the_result(self) -> None:
        spec = _spec([])
        result, _ = _call(spec, hooks=_hooks())

        payload = present(spec, result)

        assert payload["context"] == {"base": "view", "shared": "hook", "hook_out": "hi"}

    def test_the_spec_s_callable_has_the_final_say_and_reads_the_result(self) -> None:
        spec = _spec([], output_context=lambda result: {"shared": "spec", "spec_out": result.title})
        result, _ = _call(spec, hooks=_hooks())

        payload = present(spec, result)

        assert payload["context"] == {
            "base": "view",
            "shared": "spec",
            "hook_out": "hi",
            "spec_out": "hi",
        }

    def test_the_spec_s_output_callable_layers_over_the_view_with_no_hooks(self) -> None:
        spec = _spec([], output_context=lambda: {"shared": "spec"})
        result, _ = _call(spec)

        payload = present(spec, result)

        assert payload["context"] == {"base": "view", "shared": "spec"}

    def test_the_input_hooks_do_not_reach_the_output_and_the_output_hooks_not_the_input(
        self,
    ) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(seen)
        result, _ = _call(spec, hooks=_hooks())

        payload = present(spec, result)

        assert "hook_out" not in seen[0]
        assert "hook_in" not in payload["context"]


@pytest.mark.django_db
class TestWhichLayersRide:
    def test_an_input_half_alone_reaches_the_input_serializer(self) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(seen)
        result, _ = _call(spec, hooks=ViewHooks(input_serializer_context={"hook_in": "in"}))

        payload = present(spec, result)

        assert seen == [{"base": "view", "shared": "view", "hook_in": "in"}]
        assert payload["context"] == {"base": "view", "shared": "view"}

    def test_an_output_half_alone_reaches_the_output_serializer(self) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(seen)
        hooks = ViewHooks(output_serializer_context=lambda result: {"hook_out": result.title})
        result, _ = _call(spec, hooks=hooks)

        payload = present(spec, result)

        assert seen == [{"base": "view", "shared": "view"}]
        assert payload["context"] == {"base": "view", "shared": "view", "hook_out": "hi"}

    def test_context_hooks_reach_no_kwargs_callables_pool(self) -> None:
        """The view's layers ride on the edge call, never in a ``**kwargs`` callable's pool."""
        pools: list[dict[str, Any]] = []

        def service(**pool: Any) -> None:
            pools.append(pool)

        dispatch_spec(
            ServiceSpec(service=service),
            user=None,
            params={},
            view_hooks=ViewHooks(
                extra_kwargs={"x": 1},
                input_serializer_context={"hook_in": "in"},
                output_serializer_context=lambda result: {"hook_out": "out"},
            ),
        )

        assert "_serializer_context_layers" not in pools[0]

    def test_a_registered_seed_reaches_both_callables(self) -> None:
        seen: list[dict[str, Any]] = []
        spec = _spec(
            seen,
            input_context=lambda tenant: {"spec_in": tenant},
            output_context=lambda tenant: {"spec_out": tenant},
        )
        view = _View(build_offline_context(None).request)

        result = dispatch_spec(
            spec,
            user=None,
            params={"title": "hi"},
            request=view.request,
            view=view,
            pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
        )

        assert seen[0]["spec_in"] == "acme"
        assert present(spec, result)["context"]["spec_out"] == "acme"

    def test_a_many_output_s_view_hook_is_handed_no_row(self) -> None:
        """On drfs' own render the hook's ``result`` is the whole list; one row is never it."""
        handed: list[Any] = []

        def hook(result: Any) -> dict[str, Any]:
            handed.append(result)
            return {"hook_out": "set"}

        spec = ServiceSpec(
            service=lambda *, data: [SimpleNamespace(**row) for row in data],
            many=True,
            input_serializer=_spec([]).input_serializer,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
        )
        view = _View(build_offline_context(None).request)

        result = dispatch_spec(
            spec,
            user=None,
            params=[{"title": "a"}, {"title": "b"}],
            request=view.request,
            view=view,
            view_hooks=ViewHooks(output_serializer_context=hook),
        )
        payload = present(spec, result)

        assert [row["context"]["hook_out"] for row in payload] == ["set", "set"]
        assert handed == [None, None]


@pytest.mark.django_db
def test_a_callers_seed_named_like_the_edges_layer_key_shadows_nothing() -> None:
    """The view's layers travel on the edge call, not as a seed, so a caller's seed is its own."""
    seen: list[dict[str, Any]] = []
    seeds = DEFAULT_POOL_SEEDS.extend(_serializer_context_layers=lambda: None)

    dispatch_spec(
        _spec(seen),
        user=None,
        params={"title": "hi"},
        pool_seeds=seeds,
        view_hooks=ViewHooks(input_serializer_context={"hook_in": "in"}),
    )

    assert seen[0]["hook_in"] == "in"
