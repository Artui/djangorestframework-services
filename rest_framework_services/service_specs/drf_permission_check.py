"""``DRFPermissionCheck`` - a spec's DRF ``permission_classes`` as the kernel's ``PermissionCheck``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.db.models import Model
from django_service_specs.authorization.permission_check import PermissionCheck
from rest_framework.permissions import BasePermission

from rest_framework_services.service_specs.build_offline_context import build_offline_context
from rest_framework_services.service_specs.not_permitted import NotPermitted
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.url_kwarg import UrlKwarg


class DRFPermissionCheck(PermissionCheck):
    """DRF permission classes asked the kernel's three questions, off HTTP.

    The kernel asks a principal and a spec; a DRF permission class asks a
    request and a view. So each question builds the offline request and view
    [`build_offline_context`][rest_framework_services.service_specs.build_offline_context.build_offline_context]
    builds for the principal, and asks every class in order, a fresh instance
    each time, as a DRF view's ``get_permissions`` does. The first refusal
    raises the dual-base
    [`NotPermitted`][rest_framework_services.service_specs.not_permitted.NotPermitted]
    with that class's own ``message`` and ``code``, so a client is told what
    a DRF view would have told it; returning ``False`` instead would leave the
    kernel to raise its own class with the generic wording.

    **The view's ``kwargs`` are filled from the call's arguments before the
    class-level check.** A permission scoping by a route capture reads
    ``view.kwargs["project_pk"]`` in ``has_permission``, and off HTTP there is
    no URLconf to put it there; asked against an empty mapping it would refuse
    every caller it means to admit. Each method opts in to the kernel's
    ``arguments`` channel by declaring the keyword, and the names that go onto
    ``view.kwargs`` are the ones a route would have captured: every declared
    ``UrlKwarg``, and ``route_reads``, the arguments the spec's row lookup
    reads (``pk`` of an update's instance lookup, or of a retrieve), which
    [`as_kernel_spec`][rest_framework_services.service_specs.as_kernel_spec.as_kernel_spec]
    passes in. Each declared ``QueryParam`` goes onto ``request.query_params``
    instead, where a query string would have put it. A ``UrlKwarg`` or
    ``QueryParam`` the call left out, or sent as ``null``, takes its declared
    ``default``, as its docstring says a transport seeds it, and is otherwise
    absent. Where a transport asks with no arguments at all (``None``), nothing
    is filled, and the classes decide what an empty route means, as they would
    for a URL that captured nothing.

    **``has_object_permission`` asks only about a model instance**, as
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions]
    always has off HTTP; any other target is allowed here, because a DRF class
    written for a row reads attributes off it.

    **``has_scope_permission`` allows.** The kernel asks it of a bulk call's set,
    a collection's rows and a create's ``None``, and DRF has no such question:
    on HTTP, a create and a collection action run the class-level check and no
    object check, and a DRF ``has_object_permission`` handed a ``QuerySet`` or
    ``None`` would raise ``AttributeError`` or decide on the wrong object. The
    class-level check has already run, with the view's ``kwargs`` filled, by
    the time the kernel asks this one.

    No state is kept between questions: the compiled spec holding this check is
    shared by every call and every thread, so each question builds its own
    request and view.
    """

    def __init__(
        self,
        permission_classes: Sequence[type[BasePermission]],
        *,
        url_kwargs: Sequence[UrlKwarg] = (),
        query_params: Sequence[QueryParam] = (),
        route_reads: Sequence[str] = (),
    ) -> None:
        self.permission_classes: tuple[type[BasePermission], ...] = tuple(permission_classes)
        self.url_kwargs: tuple[UrlKwarg, ...] = tuple(url_kwargs)
        self.query_params: tuple[QueryParam, ...] = tuple(query_params)
        self.route_reads: tuple[str, ...] = tuple(route_reads)

    def has_permission(
        self, principal: Any, spec: Any, *, arguments: Mapping[str, Any] | None = None
    ) -> bool:
        """Whether every class's ``has_permission`` admits ``principal``, raising on the first that does not."""
        context = self._context(principal, arguments)
        for permission in self._permissions():
            if not permission.has_permission(context.request, _as_view(context)):
                _deny(permission)
        return True

    def has_object_permission(
        self,
        principal: Any,
        spec: Any,
        target: Any,
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> bool:
        """Whether every class's ``has_object_permission`` admits ``principal`` to a model ``target``."""
        if not isinstance(target, Model):
            return True
        context = self._context(principal, arguments)
        for permission in self._permissions():
            if not permission.has_object_permission(context.request, _as_view(context), target):
                _deny(permission)
        return True

    def has_scope_permission(
        self,
        principal: Any,
        spec: Any,
        scope: Any,
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> bool:
        """Allows: DRF has no question for a set or a create, as the class docstring says."""
        return True

    def _permissions(self) -> list[BasePermission]:
        return [permission_class() for permission_class in self.permission_classes]

    def _context(self, principal: Any, arguments: Mapping[str, Any] | None) -> OfflineContext:
        if arguments is None:
            return build_offline_context(principal)
        kwargs = {name: arguments[name] for name in self.route_reads if name in arguments}
        # A declared ``UrlKwarg`` outranks a lookup read of the same name, as the
        # declaration a transport routes onto ``view.kwargs``.
        kwargs.update(_routed(self.url_kwargs, arguments))
        return build_offline_context(
            principal, kwargs=kwargs, query_params=_routed(self.query_params, arguments)
        )


def _routed(
    declared: Sequence[UrlKwarg | QueryParam], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Each declared name's argument, its ``default`` where none (or ``null``) was sent."""
    routed: dict[str, Any] = {}
    for declaration in declared:
        value = arguments.get(declaration.name)
        if value is None:
            value = declaration.default
        if value is not UNSET:
            routed[declaration.name] = value
    return routed


def _as_view(context: OfflineContext) -> Any:
    # DRF's permission methods type ``view`` as ``APIView``; the offline view is
    # the structural stand-in, so it crosses the boundary as ``Any``.
    return context.view


def _deny(permission: BasePermission) -> None:
    raise NotPermitted(getattr(permission, "message", None), getattr(permission, "code", None))
