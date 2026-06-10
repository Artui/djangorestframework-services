"""``SelectorSpec`` — bundles per-action configuration for read actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from django.db.models import QuerySet
from django.db.models.query import Prefetch
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.serializers import Serializer

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_view import ServiceView

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])


@dataclass(frozen=True, kw_only=True)
class SelectorSpec(Generic[ResultT, ExtraT]):
    """All wiring for a single read action in one record.

    Used as a value in ``action_specs`` on viewsets, as the ``spec=``
    argument to :class:`SelectorListView` / :class:`SelectorRetrieveView`,
    and as the ``output_selector_spec`` field on :class:`ServiceSpec`
    (where it describes the post-mutation re-fetch).

    Generic parameters (both default to ``Any``):

    - ``ResultT`` — the selector's return type.
    - ``ExtraT`` — a ``TypedDict`` describing the keys returned by ``kwargs``.

    All fields are keyword-only: ``SelectorSpec(kind=SelectorKind.LIST,
    selector=fn)`` rather than positional. ``kind`` is required and has no
    default — see below.

    Fields:

    - **``kind``** — required :class:`SelectorKind` discriminator (``LIST`` vs
      ``RETRIEVE``). Drives the dispatcher: ``RETRIEVE`` materializes a
      QuerySet via ``.first()`` and raises
      :exc:`~rest_framework.exceptions.NotFound` on ``None`` / missing-object,
      ``LIST`` returns whatever the selector returns unchanged. Also drives
      the fail-fast check that the spec is mounted on a compatible view (a
      ``LIST`` spec on :class:`SelectorRetrieveView` raises at ``as_view()``).
      Making it explicit lets a spec be reused outside a request — from a
      management command, a cron job, or any non-DRF caller — without the
      semantics living implicitly in the call site.
    - **``selector``** — callable invoked by ``get_queryset()`` (list) or
      ``get_object()`` (retrieve). ``None`` means "use the configured
      ``queryset`` / default DRF behaviour".
    - **``allow_none``** — ``RETRIEVE``-only knob for the ``None`` /
      missing-object case. ``False`` (the default) keeps the standard
      behaviour: raise :exc:`~rest_framework.exceptions.NotFound`. ``True``
      expresses a nullable-resource contract: the standalone retrieve view
      and the retrieve viewset mixin render ``200`` with a JSON ``null``
      body, skipping the output serializer. The flag is **ignored** when
      the spec is nested — :attr:`ServiceSpec.output_selector_spec` keeps
      its authoritative-``None`` → 204 contract, and
      :attr:`ServiceSpec.instance_selector_spec` always 404s (an update
      against a missing row is not a nullable read).
    - **``output_serializer``** — DRF ``Serializer`` subclass used by
      ``get_serializer_class()`` for this action. ``None`` falls back to
      DRF's standard ``serializer_class``.
    - **``kwargs``** — callable returning extra kwargs to merge into the pool
      the selector receives. Co-locating it with the spec lets each action
      declare its own contract — no ``if self.action == ...`` branching in a
      catch-all ``get_selector_kwargs``.
    - **``permission_classes``** — overrides the calling view's
      ``permission_classes`` for the action the spec backs. ``None`` (the
      default) means "inherit the view's class-level permissions"; an empty
      sequence means "no permissions" explicitly. Forwarded through DRF's
      ``@action(permission_classes=...)`` for the ``@selector_action``
      decorator, and surfaced via ``get_permissions`` for the viewset
      mixins and standalone views. Ignored when the spec is nested under
      :attr:`ServiceSpec.output_selector_spec` — the surrounding mutation
      action's permissions apply.
    - **``output_serializer_context``** — per-spec hook for the response
      serializer's ``context=`` dict. Sits at the most-specific layer of
      the resolution chain (``view.get_serializer_context`` →
      ``view.get_output_serializer_context`` →
      ``view.get_<action>_output_serializer_context`` → spec hook), so it
      wins on overlapping keys. ``None`` (the default) leaves the three
      earlier layers intact. Selectors don't validate input, so there's
      no symmetrical ``input_serializer_context``.

      The provider is called with ``(view, request)`` positionally and may
      additionally declare the resolved data being serialized as a keyword
      parameter — ``page`` on a ``LIST`` spec (the paginated object list, or
      the full queryset when pagination is off) or ``instance`` on a
      ``RETRIEVE`` spec. It is passed only when declared (or when the
      provider accepts ``**kwargs``), so legacy ``(view, request)``
      providers are unaffected. This lets the provider run a single batched
      query against the exact objects being serialized and propagate the
      result through context — e.g.
      ``lambda view, request, *, page: {"votes": tally(page)}``. The hook
      always runs *after* the data is resolved.
    - **``select_related``** / **``prefetch_related``** / **``annotations``**
      — declarative queryset shaping applied to the selector's return value
      before it leaves :func:`dispatch_selector_for_spec`. ``select_related``
      is a sequence of relation names (forwarded as
      ``qs.select_related(*spec.select_related)``); ``prefetch_related`` is
      a sequence of relation names or :class:`Prefetch` objects;
      ``annotations`` is a mapping merged into a single ``.annotate(**...)``
      call. Use these for the common case where the same shaping applies
      every request — they're introspectable for OpenAPI / future tooling.
    - **``extend_queryset``** — dynamic escape hatch. A
      ``Callable[[QuerySet, ServiceView, Request], QuerySet]`` invoked
      *after* the declarative fields have applied, so it always sees the
      fully statically-shaped queryset. Use it when the shaping depends on
      the request (e.g. only prefetch when a query string opts in).
      Synchronous only — it manipulates the queryset's lazy expression
      tree, not the database.

    All four shaping fields require ``selector`` to be set and the selector
    to return a Django :class:`QuerySet`. Configuring shaping with no
    selector raises :exc:`ImproperlyConfigured` at ``as_view()`` time; a
    non-QuerySet return raises at request time.
    """

    kind: SelectorKind

    # The selector callable.
    selector: Callable[..., ResultT] | None = None
    # RETRIEVE-only: ``True`` renders a ``None`` resolution as 200 + JSON
    # ``null`` instead of raising ``NotFound``. Ignored on nested specs.
    allow_none: bool = False

    # Output pipeline (selector → serializer → context) plus the shaping
    # fields applied to the selector's QuerySet return.
    output_serializer: type[Serializer] | None = None
    # Called as ``(view, request)`` plus an optional resolved-data keyword
    # (``page`` for LIST, ``instance`` for RETRIEVE) when the provider
    # declares it; hence the open ``...`` parameter spec. See the class
    # docstring's ``output_serializer_context`` entry.
    output_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    select_related: Sequence[str] | None = None
    prefetch_related: Sequence[str | Prefetch] | None = None
    annotations: Mapping[str, Any] | None = None
    extend_queryset: Callable[[QuerySet[Any], ServiceView, Request], QuerySet[Any]] | None = None

    # Cross-cutting.
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
