"""Per-action serializer dispatch for ViewSets driven by ``action_specs``."""

from __future__ import annotations

from typing import Any

from rest_framework.serializers import BaseSerializer, Serializer

from rest_framework_services.dispatch.renderable_serializer_class import renderable_serializer_class
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import (
    ActionSpec,
    _ActionSpecsMixin,
    polymorphic_inputs_disagree,
    resolve_action_spec_entry,
    resolves_action_serializers,
)


class _NoBody(Serializer):
    """Describes a body of no fields: what a service taking no input accepts.

    One class for the process, not one per OPTIONS request.
    """


class ActionSerializerResolver(_ActionSpecsMixin):
    """Resolve ``get_serializer_class()`` from ``action_specs``.

    Reads the active action's entry: a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] supplies
    ``output_serializer``, a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies
    ``output_selector_spec.output_serializer``. Falls back to DRF's
    ``serializer_class``, raising the usual ``AssertionError`` when neither is set.

    OPTIONS is answered under DRF's implicit ``"metadata"`` action, once per
    POST / PUT it describes (a real request to a view's own action named
    ``metadata`` is not OPTIONS, and resolves like any other). A view that sets ``serializer_class`` is described
    by it, as DRF always has. A view built only from specs has none, and there
    the spec the described method routes to (from ``action_specs`` or stamped by
    ``@service_action`` / ``@selector_action``) answers with the body that
    method accepts:

    - a ``ServiceSpec`` with an ``input_serializer`` answers with it (a bare
      dataclass wrapped as it is for rendering);
    - a
      [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec]
      answers with its variants' ``input_serializer`` when they all declare the
      same one. Its discriminator is never asked: an OPTIONS request carries no
      body to discriminate. Variants that disagree leave the method out, unless
      the view resolves serializers itself;
    - a ``ServiceSpec`` with no input, and a ``SelectorSpec`` (a selector reads
      query parameters, never a body), answer with a serializer of no fields.

    The input is built with DRF's base serializer context only: an
    ``input_serializer`` whose fields depend on its ``input_serializer_context``
    cannot be described this way.

    Every other action resolves exactly as it did without OPTIONS in the
    picture.

    Example:

        class InvoiceViewSet(ActionSerializerResolver, GenericViewSet):
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST, output_serializer=InvoiceListSerializer,
                ),
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=InvoiceDetailSerializer,
                ),
            }
    """

    @resolves_action_serializers
    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        # A view with a ``serializer_class`` is described by it under OPTIONS,
        # exactly as before; held by
        # test_options_keeps_the_views_serializer_class_beside_a_spec_input.
        # Every other request skips this, held by
        # test_an_action_named_metadata_gets_its_output_serializer.
        if self._describing_for_options() and getattr(self, "serializer_class", None) is None:
            described = _body_serializer(self._described_action_spec())
            # ``None``: no spec answers (a handler the view defines itself), so
            # DRF's own answer stands. Held by
            # test_a_views_own_handler_without_a_spec_or_serializer_still_raises.
            if described is not None:
                return described
        # Same ``"partial_update"`` → ``"update"`` fallback as dispatch and
        # ``get_permissions``, so the three sites agree. Deliberately not the
        # handler stamp ``get_permissions`` also reads: that would change what
        # a decorated action's ``get_serializer()`` returns outside OPTIONS.
        spec = resolve_action_spec_entry(self.action_specs, self.action)
        if isinstance(spec, SelectorSpec) and spec.output_serializer is not None:
            return renderable_serializer_class(spec.output_serializer)
        if (
            isinstance(spec, ServiceSpec)
            and spec.output_selector_spec is not None
            and spec.output_selector_spec.output_serializer is not None
        ):
            return renderable_serializer_class(spec.output_selector_spec.output_serializer)
        return super().get_serializer_class()  # ty: ignore[unresolved-attribute]


def _body_serializer(spec: ActionSpec | None) -> type[BaseSerializer[Any]] | None:
    """The serializer describing the body ``spec``'s method accepts.

    ``None`` when the spec cannot say: no spec at all, or polymorphic variants
    declaring different bodies. ``check_permissions`` usually leaves the latter
    out of OPTIONS before this is asked, but not behind a view's own
    ``get_serializer_class`` that hands back to this resolver. There the
    disagreement check keeps one variant's body from being described as if it
    were the only one, held by the
    ``disagreeing-variants-behind-a-delegating-override`` row of
    test_what_options_still_cannot_describe.
    """
    if isinstance(spec, SelectorSpec):
        # A selector reads query parameters, never a body.
        return _NoBody
    if isinstance(spec, ServiceSpec):
        declared = spec.input_serializer
    elif isinstance(spec, PolymorphicServiceSpec) and not polymorphic_inputs_disagree(spec):
        declared = next(iter(spec.specs.values())).input_serializer
    else:
        return None
    return _NoBody if declared is None else renderable_serializer_class(declared)
