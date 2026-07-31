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
from rest_framework_services.types.utils import validate_metadata

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

      The provider is invoked through the framework's keyword-pool
      convention, so it declares only what it needs: ``view``, ``request``,
      and/or the resolved data being serialized — ``page`` on a ``LIST`` spec
      (the paginated object list, or the full queryset when pagination is off)
      or ``instance`` on a ``RETRIEVE`` spec — or ``**kwargs`` for the whole
      pool. Any subset works (just ``view``, just ``request``, just ``page``,
      none). This lets the provider run a single batched query against the
      exact objects being serialized and propagate the result through context
      — e.g. ``lambda *, page: {"votes": tally(page)}`` or
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
    - **``filter_set``** — transport-neutral filtering applied to the
      selector's QuerySet. Holds a ``django-filter`` ``FilterSet`` class (or
      any object honouring the same ``(data, queryset) -> .qs`` contract):
      the dispatcher calls ``filter_set(data=request.query_params,
      queryset=qs).qs``, so the *declaration* of which fields are filterable
      (with which lookups) lives on the spec while the *values* come from the
      request. Applied **after** the four shaping fields above and **before**
      the retrieve ``.first()`` materialization, so it composes with shaping
      and narrows both ``LIST`` and ``RETRIEVE`` selectors — closing the
      retrieve-path gap where ``RetrieveModelMixin`` runs no filter step.

      Because the values are read off ``request.query_params``, a
      ``FilterSet`` here is exactly what
      :class:`~django_filters.rest_framework.DjangoFilterBackend` applies
      view-side, so on the list path it **replaces** that backend rather than
      stacking with it — wiring both for one action raises at ``as_view()``.
      Replacing it means keeping its contract, so **invalid filter input is
      rejected with a 400** rather than silently ignored: the ``FilterSet`` is
      validated via ``is_valid()`` and its ``errors`` are raised as a DRF
      :exc:`~rest_framework.exceptions.ValidationError`. (Reading ``.qs``
      without validating would return the *unfiltered* queryset in
      django-filter's default non-strict mode — a bad ``?field=`` value would
      answer 200 with unfiltered rows.) Only enforced when the duck-typed
      object actually exposes ``is_valid``; a bare
      ``(data, queryset) -> .qs`` stand-in that doesn't opt into validation
      keeps its pass-through behaviour.
      The dispatcher also forwards the ``request`` into the FilterSet when its
      constructor declares one (as ``django-filter``'s does), so a request-scoped
      FilterSet — ``self.request.user`` scoping, a request-aware
      ``ModelChoiceFilter`` ``queryset`` — sees the same ``self.request`` it would
      behind ``DjangoFilterBackend`` rather than ``None``. That request is real on
      the HTTP / MCP paths and a synthetic off-HTTP one whose ``user`` and
      ``query_params`` are faithful (headers / session are best-effort there); a
      bare ``(data, queryset)`` stand-in never receives it. ``None`` (the default)
      applies no filtering. Reach for ``filter_set`` only when the selector returns
      a QuerySet; when it returns an aggregate / computed object the ``?param``
      values are computation inputs — use ``kwargs`` / ``get_selector_kwargs()``
      instead.

    - **``metadata``** — consumer-owned, framework-opaque mapping.

      ⚠ **The framework never reads it.** No known keys, no per-key
      validation, no defaulting, no effect on the generated JSON Schema or
      OpenAPI. Validation is shape-only: a non-``Mapping`` raises
      :exc:`~django.core.exceptions.ImproperlyConfigured` at construction.

      It is here so a project can attach its own per-operation facts — read
      back later by its own permission class, scoping helper, or audit hook —
      to the thing they describe, instead of a name-keyed side table that
      drifts the day a spec is renamed. Reachable wherever the spec is:
      ``view.action_specs[view.action].metadata`` inside a permission class
      (which receives ``(request, view)`` and so has the spec but knows no
      registry or name), or ``entry.spec.metadata`` from a
      :class:`~rest_framework_services.types.registered_spec.RegisteredSpec`.
      That two-sided reachability is why it lives on the spec rather than on
      the registry entry, where ``tags`` handles the boolean-ish labels this
      is *not* for.

      Two things it deliberately doesn't do. It never merges or inherits: a
      :class:`ServiceSpec` and its ``output_selector_spec`` are independent
      objects with independent metadata. And it is stored exactly as given —
      the spec is frozen, the mapping is not, and the library neither copies
      nor deep-freezes it, so pass something you don't mutate. ``None`` (the
      default) means "not declared", which stays distinguishable from a
      declared empty mapping.

    All five shaping fields (``select_related`` / ``prefetch_related`` /
    ``annotations`` / ``extend_queryset`` / ``filter_set``) require
    ``selector`` to be set and the selector to return a Django
    :class:`QuerySet`. Configuring any of them with no selector raises
    :exc:`ImproperlyConfigured` at ``as_view()`` time; a non-QuerySet return
    raises at request time.
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
    # Invoked through the framework's keyword pool: the provider declares any
    # subset of ``view`` / ``request`` plus the resolved-data extra (``page``
    # for LIST, ``instance`` for RETRIEVE), or ``**kwargs``; hence the open
    # ``...`` parameter spec. See the class docstring's
    # ``output_serializer_context`` entry.
    output_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    select_related: Sequence[str] | None = None
    prefetch_related: Sequence[str | Prefetch] | None = None
    annotations: Mapping[str, Any] | None = None
    extend_queryset: Callable[[QuerySet[Any], ServiceView, Request], QuerySet[Any]] | None = None
    # Transport-neutral filtering: a django-filter ``FilterSet`` (or any
    # ``(data, queryset) -> .qs`` object) applied after the shaping fields,
    # before the retrieve ``.first()``. Replaces ``DjangoFilterBackend`` on
    # the list path. Typed ``Any`` so ``types/`` never imports django-filter
    # (the dependency-sink rule); services applies it by duck typing.
    filter_set: Any | None = None

    # Cross-cutting. ``kwargs`` is invoked through the framework's keyword
    # pool, so it declares any subset of ``view`` / ``request`` (or ``**kwargs``)
    # — hence the open ``...`` parameter spec.
    kwargs: Callable[..., ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    # Consumer-owned, framework-opaque. The name fits none of the three
    # patterns in CLAUDE.md (it wraps no Django/DRF concept, configures no
    # serialization phase, resolves no nested spec), so it is chosen for the
    # convention it does match: ``dataclasses.field(metadata=...)``, whose
    # semantics — a read-only mapping the library carries but never reads —
    # are exactly these. Not ``extras``: that already names the keyword pool
    # selectors and services receive (``**extras: Unpack[...]``).
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.metadata, label="SelectorSpec")
