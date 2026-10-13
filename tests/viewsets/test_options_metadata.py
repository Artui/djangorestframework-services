"""OPTIONS against a spec-backed viewset.

DRF sets ``self.action = "metadata"`` for OPTIONS — an *implicit* action with no
handler method behind it. Anything that assumes ``self.action`` names a bound
method breaks there, and nothing in the suite exercised it, so the whole path
went unnoticed.

**Wider than explicit OPTIONS calls.** A CORS preflight is an OPTIONS request,
and ``django-cors-headers`` only short-circuits paths matching
``CORS_URLS_REGEX`` that also carry ``Access-Control-Request-Method`` — every
other route reaches the view. Easy to miss locally, easy to hit in production on
a subset of routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet, ViewSet

from rest_framework_services import (
    ActionSerializerResolver,
    PolymorphicServiceSpec,
    SelectorKind,
    SelectorSpec,
    SelectorViewSet,
    ServiceSpec,
    ServiceUpdateMixin,
    ServiceViewSet,
    selector_action,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _AuthorIn:
    name: str


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return False


class _RenameIn(serializers.Serializer):
    new_name = serializers.CharField()


def _rename_authors(*, data: Any) -> None:  # pragma: no cover - never called
    raise AssertionError


def _reindex_authors() -> None:  # pragma: no cover - never called
    raise AssertionError


def _options(view: Any) -> Any:
    return view(factory.options("/"))


# ----- the defect -----


@pytest.mark.django_db
def test_options_on_a_service_viewset_does_not_raise() -> None:
    """The reported bug: ``AttributeError: … has no attribute 'metadata'``.

    ``get_permissions`` fell through to a ``getattr(self, self.action)`` with no
    default, looking for a handler that DRF documents as implicit. The
    ``AttributeError`` is not an ``APIException``, so ``handle_exception``
    re-raises it and the request ends as an unhandled 500 — before any
    permission was evaluated.
    """

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    assert _options(_View.as_view({"get": "list"})).status_code == 200


@pytest.mark.django_db
def test_options_on_a_selector_viewset_does_not_raise() -> None:
    class _View(SelectorViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    assert _options(_View.as_view({"get": "list"})).status_code == 200


@pytest.mark.django_db
def test_a_plain_drf_viewset_is_the_control() -> None:
    """Establishes that 200 is DRF's own behaviour, not something invented
    here — the mixin was the only thing standing between OPTIONS and it."""

    class _View(ViewSet):
        def list(self, request: Any) -> Any:  # pragma: no cover - never called
            raise AssertionError

    assert _options(_View.as_view({"get": "list"})).status_code == 200


# ----- with specs attached, which is the real deployment -----


@pytest.mark.django_db
def test_options_works_with_action_specs_declared() -> None:
    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(service=_create_author, input_serializer=AuthorSerializer)
        }

    assert _options(_View.as_view({"post": "create"})).status_code == 200


@pytest.mark.django_db
def test_options_works_with_a_selector_spec_declared() -> None:
    class _View(SelectorViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"list": SelectorSpec(kind=SelectorKind.LIST, selector=_list_authors)}

    assert _options(_View.as_view({"get": "list"})).status_code == 200


# ----- the permissions that do apply -----


@pytest.mark.django_db
def test_options_is_gated_by_the_views_own_permission_classes() -> None:
    """Fail-closed, and it must stay that way. A spec's
    ``permission_classes`` describe *that action*; ``metadata`` is not one, so
    the view's own classes are what OPTIONS answers to — which is exactly DRF's
    behaviour and not a hole opened by the fix."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        permission_classes = [_DenyAll]

    assert _options(_View.as_view({"get": "list"})).status_code in (401, 403)


@pytest.mark.django_db
def test_an_actions_spec_permissions_do_not_leak_into_options() -> None:
    """The converse: a locked-down ``create`` must not make OPTIONS deny, and a
    permissive one must not make it allow. They are unrelated questions."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(
                service=_create_author,
                input_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )
        }

    assert _options(_View.as_view({"post": "create"})).status_code == 200


@pytest.mark.django_db
def test_a_real_action_still_resolves_its_spec_permissions() -> None:
    """The guard must not have made ``get_permissions`` stop finding specs."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(
                service=_create_author,
                input_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )
        }

    response = _View.as_view({"post": "create"})(factory.post("/", {"name": "Ada"}))
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_options_answers_with_metadata_rather_than_an_empty_body() -> None:
    """It is a real DRF metadata response, not merely a non-500."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    response = _options(_View.as_view({"get": "list"}))
    assert "name" in response.data
    assert "renders" in response.data


# ----- the general shape of the guard -----


@pytest.mark.django_db
def test_any_action_without_a_bound_handler_is_survivable() -> None:
    """``metadata`` is the case DRF guarantees, but the branch was unsafe for
    *any* action name with no attribute behind it. Pinned generally so a future
    implicit action does not reopen this."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    view = _View()
    view.action = "not-a-method"
    view.request = None  # type: ignore[assignment]
    assert view.get_permissions() is not None


@pytest.mark.django_db
def test_a_method_the_viewset_does_not_route_is_refused_rather_than_crashing() -> None:
    """DRF sets ``self.action = None`` for a method missing from the action
    map and checks permissions before it refuses the method. Resolving a spec
    for ``None`` must answer ``None``, not look up a handler by that name, or
    the 405 becomes a ``TypeError`` and a 500."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    assert _View.as_view({"get": "list"})(factory.put("/")).status_code == 405


# ----- describing a no-content action -----


@pytest.mark.django_db
def test_options_describes_a_no_content_service_by_its_input() -> None:
    """A spec with no output re-read declares no output serializer, and a view
    built only from specs sets no ``serializer_class``. DRF's metadata asks
    ``get_serializer_class()`` for every POST / PUT it describes, so the
    resolver fell through to DRF's ``AssertionError`` and OPTIONS ended in a
    500."""

    class _View(ServiceViewSet):
        action_specs = {"create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)}

    response = _options(_View.as_view({"post": "create"}))
    assert response.status_code == 200
    assert set(response.data["actions"]["POST"]) == {"new_name"}


@pytest.mark.django_db
def test_options_describes_a_dataclass_input() -> None:
    class _View(ServiceViewSet):
        action_specs = {"create": ServiceSpec(service=_create_author, input_serializer=_AuthorIn)}

    response = _options(_View.as_view({"post": "create"}))
    assert set(response.data["actions"]["POST"]) == {"name"}


@pytest.mark.django_db
def test_options_keeps_the_views_serializer_class_beside_a_spec_input() -> None:
    """A view that sets ``serializer_class`` is described by it, as DRF always
    has, even where the spec declares an input of its own. The spec's input is
    only the answer for a view that has no ``serializer_class`` to give."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)}

    response = _options(_View.as_view({"post": "create"}))
    assert _described(response) == {"POST": {"id", "name"}}


class _NeedsTenant(serializers.Serializer):
    """An input whose fields cannot be built without a context key.

    OPTIONS builds the described serializer with DRF's base context only, so
    the key a real request would supply is missing there.
    """

    new_name = serializers.CharField()

    def get_fields(self) -> Any:
        fields = super().get_fields()
        _ = self.context["tenant"]
        return fields


def _tenant_context(**kwargs: Any) -> dict[str, Any]:  # pragma: no cover - never called
    return {"tenant": "acme"}


@pytest.mark.django_db
def test_a_context_dependent_input_beside_a_serializer_class_is_not_built() -> None:
    """The spec's ``input_serializer_context`` provider runs on a real request,
    never under OPTIONS. Describing the view by its ``serializer_class`` keeps
    OPTIONS from building an input that would fail without it."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(
                service=_rename_authors,
                input_serializer=_NeedsTenant,
                input_serializer_context=_tenant_context,
            )
        }

    response = _options(_View.as_view({"post": "create"}))
    assert response.status_code == 200
    assert _described(response) == {"POST": {"id", "name"}}


@pytest.mark.django_db
def test_a_context_dependent_input_from_a_view_hook_is_not_built() -> None:
    """The same through the view's own ``get_input_serializer_context`` hook."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {
            "create": ServiceSpec(service=_rename_authors, input_serializer=_NeedsTenant)
        }

        def get_input_serializer_context(self) -> dict[str, Any]:  # pragma: no cover
            return {**super().get_input_serializer_context(), "tenant": "acme"}

    response = _options(_View.as_view({"post": "create"}))
    assert response.status_code == 200
    assert _described(response) == {"POST": {"id", "name"}}


@pytest.mark.django_db
def test_options_without_a_spec_input_keeps_the_views_serializer_class() -> None:
    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"create": ServiceSpec(service=_reindex_authors)}

    response = _options(_View.as_view({"post": "create"}))
    assert set(response.data["actions"]["POST"]) == {"id", "name"}


@pytest.mark.django_db
def test_options_leaves_out_a_post_no_spec_serves() -> None:
    """``ServiceViewSet`` defines ``create`` whether or not a spec serves it,
    so ``{"post": "create"}`` routes a POST the view refuses with a 405. That
    POST used to be described by the ``serializer_class``, advertising a
    method the view does not accept; it is now left out, as DRF leaves out a
    method a view refuses."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    view = _View.as_view({"post": "create"})
    response = _options(view)
    assert response.status_code == 200
    assert _described(response) == {}
    assert view(factory.post("/", {}, format="json")).status_code == 405


@pytest.mark.django_db
def test_a_views_own_handler_without_a_spec_is_described() -> None:
    """Only a handler this package defines is known to refuse without a spec.
    A view's own ``create`` serves the POST itself, so it is described by the
    ``serializer_class`` exactly as before."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

        def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError

    response = _options(_View.as_view({"post": "create"}))
    assert _described(response) == {"POST": {"id", "name"}}


@pytest.mark.django_db
def test_a_views_own_handler_without_a_spec_or_serializer_still_raises() -> None:
    """With neither a spec nor a ``serializer_class``, nothing can describe a
    handler the view wrote itself, and DRF's ``AssertionError`` stands, as it
    always did. Answering ``None`` in its place would fail later and less
    legibly."""

    class _View(ServiceViewSet):
        def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError

    with pytest.raises(AssertionError, match="serializer_class"):
        _options(_View.as_view({"post": "create"}))


# ----- every spec-only shape, as one table -----
#
# Each row is a viewset built only from specs (no ``serializer_class``, and no
# ``queryset`` unless the row says so), answered under OPTIONS. ``described``
# is DRF's ``actions`` block reduced to field names per method; a method it
# leaves out is one the view chose not to describe. Adding a shape is one row.


class _OtherIn(serializers.Serializer):
    other = serializers.CharField()


def _reread_author(*, result: Any) -> Any:  # pragma: no cover - never called
    raise AssertionError


def _rename_one(*, instance: Any, data: Any) -> None:  # pragma: no cover - never called
    raise AssertionError


def _lookup_author(*, pk: Any) -> Any:
    # A real lookup: as a retrieve selector it answers DRF's object probe, as
    # it always has.
    return Author.objects.get(pk=pk)


def _always_a(*, data: Any) -> str:  # pragma: no cover - never called
    raise AssertionError


@service_action(ServiceSpec(service=_rename_authors, input_serializer=_RenameIn))
def rename(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...


@selector_action(
    SelectorSpec(
        kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
    ),
    methods=["post"],
    detail=False,
)
def search(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...


@service_action(
    ServiceSpec(service=_rename_authors, input_serializer=_RenameIn), methods=["put"], detail=False
)
def bulk_rename(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...


def _never_selected(*, pk: Any) -> Any:  # pragma: no cover - never called
    # OPTIONS must not run an instance selector: it is the PUT's own lookup,
    # with whatever cost or side effect that carries, not DRF's probe.
    raise AssertionError("the instance selector ran under OPTIONS")


def _instance_put() -> ServiceSpec:
    return ServiceSpec(
        service=_rename_one,
        input_serializer=_RenameIn,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_never_selected),
    )


def _view(attrs: dict[str, Any]) -> Any:
    # ``"bases"`` swaps the composition; every other key is a class attribute.
    bases = attrs.get("bases", (ServiceViewSet,))
    body = {key: value for key, value in attrs.items() if key != "bases"}
    return type("_View", bases, {"__module__": __name__, **body})


def _described(response: Any) -> dict[str, set[str]]:
    return {method: set(fields) for method, fields in response.data.get("actions", {}).items()}


def _distinct_inputs() -> PolymorphicServiceSpec:
    return PolymorphicServiceSpec(
        discriminator=_always_a,
        specs={
            "a": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn),
            "b": ServiceSpec(service=_rename_authors, input_serializer=_OtherIn),
        },
    )


_SPEC_ONLY_SHAPES = [
    pytest.param(
        {
            "action_specs": {
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)
            }
        },
        {"post": "create"},
        {"POST": {"new_name"}},
        id="create-no-output-serializer-input",
    ),
    pytest.param(
        {
            "action_specs": {
                "create": ServiceSpec(service=_create_author, input_serializer=_AuthorIn)
            }
        },
        {"post": "create"},
        {"POST": {"name"}},
        id="create-no-output-dataclass-input",
    ),
    pytest.param(
        # The cause is that no spec matches ``"metadata"``, not a missing
        # output: an output serializer did not save this shape either.
        {
            "action_specs": {
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=_RenameIn,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        selector=_reread_author,
                        output_serializer=AuthorSerializer,
                    ),
                )
            }
        },
        {"post": "create"},
        {"POST": {"new_name"}},
        id="create-with-output-serializer",
    ),
    pytest.param(
        # Accepts no body, and says so, rather than asserting for want of a
        # ``serializer_class`` it has no use for.
        {"action_specs": {"create": ServiceSpec(service=_reindex_authors)}},
        {"post": "create"},
        {"POST": set()},
        id="create-no-input",
    ),
    pytest.param(
        # The spec lives on the handler, not in ``action_specs``.
        {"rename": rename},
        {"post": "rename"},
        {"POST": {"new_name"}},
        id="service-action-post",
    ),
    pytest.param(
        {"queryset": Author.objects.all(), "rename": rename},
        {"post": "rename"},
        {"POST": {"new_name"}},
        id="service-action-post-with-queryset",
    ),
    pytest.param(
        # A selector reads query parameters, never a body, so a POST routed to
        # one accepts no fields.
        {"bases": (SelectorViewSet,), "search": search},
        {"post": "search"},
        {"POST": set()},
        id="selector-action-post",
    ),
    pytest.param(
        # Every variant accepts the same body, so that body is described
        # without asking the discriminator which one an OPTIONS request is.
        {
            "action_specs": {
                "create": PolymorphicServiceSpec(
                    discriminator=_always_a,
                    specs={
                        "a": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn),
                        "b": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn),
                    },
                )
            }
        },
        {"post": "create"},
        {"POST": {"new_name"}},
        id="polymorphic-shared-input",
    ),
    pytest.param(
        # Variants disagree and the view names no serializer of its own: no
        # one body can be described, and picking a variant would be a guess,
        # so the POST is left out rather than described as taking nothing.
        {"action_specs": {"create": _distinct_inputs()}},
        {"post": "create"},
        {},
        id="polymorphic-distinct-inputs",
    ),
    pytest.param(
        # PUT is checked against a real row, then described by its input.
        {
            "queryset": Author.objects.all(),
            "action_specs": {
                "update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)
            },
        },
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="put-with-queryset",
    ),
    pytest.param(
        # DRF's metadata calls ``get_object()`` for every PUT, as its
        # object-permission probe, and with no ``queryset`` its lookup cannot
        # run. A real PUT resolves this target through the instance selector
        # instead, and checks object permissions on that row, so the probe is
        # not skipped (that would describe a PUT the caller may be refused):
        # the PUT is left out, where it used to end the response in a 500.
        {"action_specs": {"update": _instance_put()}},
        {"put": "update"},
        {},
        id="put-instance-selector-no-queryset",
    ),
    pytest.param(
        # With a ``queryset`` the probe looks the row up, as it always has.
        {"queryset": Author.objects.all(), "action_specs": {"update": _instance_put()}},
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="put-instance-selector-with-queryset",
    ),
    pytest.param(
        # And a view with a ``serializer_class`` is described by it.
        {
            "queryset": Author.objects.all(),
            "serializer_class": AuthorSerializer,
            "action_specs": {"update": _instance_put()},
        },
        {"put": "update"},
        {"PUT": {"id", "name"}},
        id="put-instance-selector-with-queryset-and-serializer-class",
    ),
    pytest.param(
        # A retrieve selector answers ``get_object()`` before DRF's lookup
        # does, so the probe finds the row through it, as it always has.
        {
            "action_specs": {
                "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup_author),
                "update": _instance_put(),
            }
        },
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="put-instance-selector-beside-a-retrieve-selector",
    ),
    pytest.param(
        # A list selector supplies the queryset the lookup filters.
        {
            "action_specs": {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                ),
                "update": _instance_put(),
            }
        },
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="put-instance-selector-beside-a-list-selector",
    ),
    pytest.param(
        # As does a view's own ``get_queryset``.
        {
            "get_queryset": lambda self: Author.objects.all(),
            "action_specs": {"update": _instance_put()},
        },
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="put-instance-selector-beside-a-get-queryset",
    ),
    pytest.param(
        # A composition carrying no retrieve mixin reaches the same answer as
        # the first row.
        {
            "bases": (ServiceUpdateMixin, ActionSerializerResolver, GenericViewSet),
            "action_specs": {"update": _instance_put()},
        },
        {"put": "update"},
        {},
        id="put-instance-selector-without-a-retrieve-mixin",
    ),
    pytest.param(
        # Bulk: a real PUT fetches no single row either, and the probe still
        # cannot run without a ``queryset``.
        {
            "action_specs": {
                "update": ServiceSpec(
                    service=_rename_authors, input_serializer=_RenameIn, many=True
                )
            }
        },
        {"put": "update"},
        {},
        id="put-bulk-no-queryset",
    ),
    pytest.param(
        # The same for a target set chosen by a collection selector.
        {
            "action_specs": {
                "update": ServiceSpec(
                    service=_rename_authors,
                    input_serializer=_RenameIn,
                    collection_selector_spec=SelectorSpec(
                        kind=SelectorKind.LIST, selector=_list_authors
                    ),
                )
            }
        },
        {"put": "update"},
        {},
        id="put-collection-selector-no-queryset",
    ),
    pytest.param(
        # Variants may differ in where their target comes from, so without a
        # discriminated body the row is probed as DRF always has.
        {
            "queryset": Author.objects.all(),
            "action_specs": {
                "update": PolymorphicServiceSpec(
                    discriminator=_always_a,
                    specs={
                        "a": ServiceSpec(service=_rename_one, input_serializer=_RenameIn),
                        "b": ServiceSpec(service=_rename_one, input_serializer=_RenameIn),
                    },
                )
            },
        },
        {"put": "update"},
        {"PUT": {"new_name"}},
        id="polymorphic-put-with-queryset",
    ),
    pytest.param(
        # What a router builds: it maps every method whose action the class
        # defines, so the detail route offers PUT though no spec serves it and
        # a real PUT is refused with a 405. The PUT is left out, as DRF leaves
        # out a method a view refuses; it used to be described as taking no
        # fields, which advertised a method the view does not accept.
        {
            "action_specs": {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                ),
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn),
            }
        },
        {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"},
        {},
        id="router-detail-route-with-no-update-spec",
    ),
    pytest.param(
        # The detail route of a create-only registration: nothing finds a row
        # there, which used to end the response in a 500.
        {
            "action_specs": {
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)
            }
        },
        {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"},
        {},
        id="router-detail-route-of-a-create-only-spec",
    ),
    pytest.param(
        # A PATCH spec does not serve PUT: ``partial_update`` falls back to
        # ``update``, never the other way round, so the routed PUT is refused.
        {
            "action_specs": {
                "partial_update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)
            }
        },
        {"put": "update", "patch": "partial_update"},
        {},
        id="router-put-beside-a-patch-only-spec",
    ),
    pytest.param(
        {"action_specs": {"partial_update": _instance_put()}},
        {"put": "update", "patch": "partial_update"},
        {},
        id="router-put-beside-a-patch-only-instance-selector-spec",
    ),
    pytest.param(
        # The list route of the same registration, with no ``create`` spec.
        {
            "action_specs": {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                )
            }
        },
        {"get": "list", "post": "create"},
        {},
        id="router-list-route-with-no-create-spec",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("attrs", "actions", "described"), _SPEC_ONLY_SHAPES)
def test_options_describes_every_spec_only_shape(
    attrs: dict[str, Any], actions: dict[str, str], described: dict[str, set[str]]
) -> None:
    """Almost every row raised DRF's ``serializer_class`` or ``queryset``
    ``AssertionError`` out of the metadata path, so OPTIONS (and every CORS
    preflight reaching the view) ended in a 500. The rows that set a
    ``serializer_class`` pin what it already answered."""
    author = Author.objects.create(name="Ada")
    response = _view(attrs).as_view(actions)(factory.options("/"), pk=author.pk)
    assert response.status_code == 200
    assert _described(response) == described


_NON_DETAIL_PUTS = [
    pytest.param({"bulk_rename": bulk_rename}, id="non-detail-put-no-queryset"),
    pytest.param(
        {
            "action_specs": {
                "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup_author)
            },
            "bulk_rename": bulk_rename,
        },
        id="non-detail-put-beside-a-retrieve-selector",
    ),
    pytest.param(
        {"queryset": Author.objects.all(), "bulk_rename": bulk_rename},
        id="non-detail-put-with-queryset",
    ),
    pytest.param(
        {"bases": (ActionSerializerResolver, GenericViewSet), "bulk_rename": bulk_rename},
        id="non-detail-put-without-a-retrieve-mixin",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("attrs", _NON_DETAIL_PUTS)
def test_options_describes_a_non_detail_put(attrs: dict[str, Any]) -> None:
    """A ``detail=False`` action routes PUT on a URL with no lookup kwarg, and
    a real PUT there never fetches a row. DRF's object probe has no row to
    fetch either (it asserted on the missing ``queryset`` or kwarg, or handed
    the retrieve selector no ``pk``), so it is skipped for such an action."""
    response = _view(attrs).as_view({"put": "bulk_rename"})(factory.options("/"))
    assert response.status_code == 200
    assert _described(response) == {"PUT": {"new_name"}}


_REFUSED_METHODS = [
    pytest.param(
        {
            "action_specs": {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                )
            }
        },
        {"get": "retrieve", "put": "update"},
        "put",
        id="put-with-no-update-spec",
    ),
    pytest.param(
        {
            "action_specs": {
                "partial_update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)
            }
        },
        {"put": "update", "patch": "partial_update"},
        "put",
        id="put-beside-a-patch-only-spec",
    ),
    pytest.param(
        {"action_specs": {"partial_update": _instance_put()}},
        {"put": "update", "patch": "partial_update"},
        "put",
        id="put-beside-a-patch-only-instance-selector-spec",
    ),
    pytest.param(
        {
            "action_specs": {
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)
            }
        },
        {"get": "retrieve", "put": "update"},
        "put",
        id="put-on-a-create-only-spec",
    ),
    pytest.param(
        {"action_specs": {}},
        {"get": "list", "post": "create"},
        "post",
        id="post-with-no-create-spec",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("attrs", "actions", "method"), _REFUSED_METHODS)
def test_a_method_no_spec_serves_is_left_out_and_still_refused(
    attrs: dict[str, Any], actions: dict[str, str], method: str
) -> None:
    """What OPTIONS leaves out is exactly what the view refuses."""
    author = Author.objects.create(name="Ada")
    view = _view(attrs).as_view(actions)
    assert method.upper() not in _described(view(factory.options("/"), pk=author.pk))
    request = getattr(factory, method)("/", {"new_name": "x"}, format="json")
    assert view(request, pk=author.pk).status_code == 405


_CONTROL_SHAPES = [
    pytest.param(
        {
            "action_specs": {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                )
            }
        },
        {"get": "list"},
        id="list-only",
    ),
    pytest.param(
        {"action_specs": {"destroy": ServiceSpec(service=_reindex_authors)}},
        {"delete": "destroy"},
        id="destroy-only",
    ),
    pytest.param(
        {
            "action_specs": {
                "partial_update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)
            }
        },
        {"patch": "partial_update"},
        id="patch-only",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("attrs", "actions"), _CONTROL_SHAPES)
def test_shapes_describing_no_post_or_put_answer_as_before(
    attrs: dict[str, Any], actions: dict[str, str]
) -> None:
    """DRF describes only POST and PUT, so these never reached the resolver.
    Pinned beside the matrix so it is visible which shapes were never broken."""
    author = Author.objects.create(name="Ada")
    response = _view(attrs).as_view(actions)(factory.options("/"), pk=author.pk)
    assert response.status_code == 200
    assert _described(response) == {}


@pytest.mark.django_db
def test_a_polymorphic_body_falls_back_to_the_views_serializer_class() -> None:
    """Where the variants disagree, the view's own serializer is what OPTIONS
    has always answered with, and it still is."""
    attrs = {
        "serializer_class": AuthorSerializer,
        "queryset": Author.objects.all(),
        "action_specs": {"create": _distinct_inputs()},
    }
    response = _options(_view(attrs).as_view({"post": "create"}))
    assert _described(response) == {"POST": {"id", "name"}}


@pytest.mark.django_db
def test_a_locked_down_action_still_has_its_body_described() -> None:
    """OPTIONS checks the view's permissions, never the described action's, so
    a ``create`` closed by its spec must not hide the POST description. Status
    alone cannot show it: DRF drops a method it was refused without a word."""
    attrs = {
        "action_specs": {
            "create": ServiceSpec(
                service=_rename_authors,
                input_serializer=_RenameIn,
                permission_classes=[_DenyAll],
            )
        }
    }
    response = _options(_view(attrs).as_view({"post": "create"}))
    assert response.status_code == 200
    assert _described(response) == {"POST": {"new_name"}}


@pytest.mark.django_db
def test_a_put_resolving_through_get_object_is_still_checked_against_the_row() -> None:
    """Wherever DRF's object probe can run, it runs: a missing row drops the
    PUT description, as it always has."""
    attrs = {
        "queryset": Author.objects.all(),
        "action_specs": {"update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)},
    }
    response = _view(attrs).as_view({"put": "update"})(factory.options("/"), pk=0)
    assert response.status_code == 200
    assert _described(response) == {}


_PUTS_OPTIONS_CANNOT_LOOK_UP = [
    pytest.param(
        ServiceSpec(service=_rename_one, input_serializer=_RenameIn), id="through-get-object"
    ),
    pytest.param(
        PolymorphicServiceSpec(
            discriminator=_always_a,
            specs={"a": _instance_put(), "b": _instance_put()},
        ),
        id="polymorphic",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("spec", _PUTS_OPTIONS_CANNOT_LOOK_UP)
def test_a_put_options_cannot_look_up_still_raises(spec: Any) -> None:
    """With no ``queryset``, a PUT whose real request fetches its row through
    ``get_object()`` fails there too, and a polymorphic PUT may, depending on
    the variant a body picks. OPTIONS answers both as it always did: DRF's
    ``AssertionError``. Only a PUT known to resolve its row elsewhere is left
    out instead."""
    author = Author.objects.create(name="Ada")
    view = _view({"action_specs": {"update": spec}}).as_view({"put": "update"})
    with pytest.raises(AssertionError, match="queryset"):
        view(factory.options("/"), pk=author.pk)


def _own_create(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
    raise AssertionError


_STILL_UNDESCRIBABLE = [
    pytest.param(
        # Routed on a URL with no lookup kwarg, and DRF's probe has a
        # ``queryset`` to look the kwarg up in.
        {
            "queryset": Author.objects.all(),
            "action_specs": {
                "update": ServiceSpec(
                    service=_rename_authors, input_serializer=_RenameIn, many=True
                )
            },
        },
        {"put": "update"},
        AssertionError,
        "URL keyword argument",
        id="bulk-put-with-queryset-on-a-list-route",
    ),
    pytest.param(
        {
            "action_specs": {
                "create": ServiceSpec(
                    service=_rename_authors,
                    input_serializer=_NeedsTenant,
                    input_serializer_context=_tenant_context,
                )
            }
        },
        {"post": "create"},
        KeyError,
        "tenant",
        id="context-dependent-input-without-a-serializer-class",
    ),
    pytest.param(
        {
            "bases": (GenericViewSet, ActionSerializerResolver),
            "action_specs": {
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)
            },
            "create": _own_create,
        },
        {"post": "create"},
        AssertionError,
        "serializer_class",
        id="resolver-after-generic-viewset",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("attrs", "actions", "error", "match"), _STILL_UNDESCRIBABLE)
def test_what_options_still_cannot_describe(
    attrs: dict[str, Any], actions: dict[str, str], error: type[Exception], match: str
) -> None:
    """The shapes the OPTIONS docs list as still ending in a 500. Each ended in
    a 500 before too, though the context-dependent input now fails inside its
    own ``get_fields`` rather than on DRF's ``serializer_class`` assertion. A
    change that starts describing one should update those docs too."""
    with pytest.raises(error, match=match):
        _options(_view(attrs).as_view(actions))


def _bare(attrs: dict[str, Any], action: str, method: str, **kwargs: Any) -> Any:
    # A view instance set up the way ``initial()`` leaves it, to call one
    # method of it directly on a request that is not OPTIONS.
    view = _view(attrs)(action_map={method: action})
    view.action = action
    view.request = getattr(factory, method)("/")
    view.kwargs = kwargs
    view.format_kwarg = None
    return view


@pytest.mark.django_db
def test_outside_options_get_object_looks_up_for_a_non_detail_put() -> None:
    """The probe is skipped for DRF's metadata alone. A real request that calls
    ``get_object()`` (a hook reading the row, say) still gets the row."""
    author = Author.objects.create(name="Ada")
    view = _bare(
        {"queryset": Author.objects.all(), "bulk_rename": bulk_rename},
        "bulk_rename",
        "put",
        pk=author.pk,
    )
    assert view.get_object() == author


@pytest.mark.django_db
def test_outside_options_a_missing_queryset_still_raises() -> None:
    """Only OPTIONS turns a missing ``queryset`` into a left-out PUT. A real
    request reaching ``get_queryset()`` still gets DRF's ``AssertionError``."""
    view = _bare({"action_specs": {"update": _instance_put()}}, "update", "put")
    with pytest.raises(AssertionError, match="queryset"):
        view.get_queryset()


def test_outside_options_check_permissions_refuses_nothing_new() -> None:
    """Leaving a method out is for DRF's metadata alone. A real POST to a
    polymorphic ``create`` with no ``serializer_class`` is checked as it always
    was, and its discriminator picks the body."""
    view = _bare({"action_specs": {"create": _distinct_inputs()}}, "create", "post")
    view.check_permissions(view.request)


def test_outside_options_a_handler_stamped_spec_does_not_pick_the_serializer() -> None:
    """Only the metadata path reads a decorator's spec to pick a serializer.
    Every real action resolves exactly as it did: from ``action_specs``, then
    ``serializer_class``."""

    @selector_action(SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_RenameIn))
    def lookup(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...

    view = _view({"serializer_class": AuthorSerializer, "lookup": lookup})()
    view.action = "lookup"
    assert view.get_serializer_class() is AuthorSerializer
