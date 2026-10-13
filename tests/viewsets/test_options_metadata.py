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
def test_options_prefers_the_input_over_the_views_serializer_class() -> None:
    """The POST entry describes the body POST accepts, which is the spec's
    input, not whatever the view renders elsewhere."""

    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)}

    response = _options(_View.as_view({"post": "create"}))
    assert set(response.data["actions"]["POST"]) == {"new_name"}


@pytest.mark.django_db
def test_options_without_a_spec_input_keeps_the_views_serializer_class() -> None:
    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()
        action_specs = {"create": ServiceSpec(service=_reindex_authors)}

    response = _options(_View.as_view({"post": "create"}))
    assert set(response.data["actions"]["POST"]) == {"id", "name"}


@pytest.mark.django_db
def test_options_for_an_unspecced_post_keeps_the_views_serializer_class() -> None:
    class _View(ServiceViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

    response = _options(_View.as_view({"post": "create"}))
    assert set(response.data["actions"]["POST"]) == {"id", "name"}


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
    # A real lookup, so a row that wrongly probes through it describes the PUT
    # rather than failing for a reason of the test's own making.
    return Author.objects.get(pk=pk)


def _always_a(*, data: Any) -> str:  # pragma: no cover - never called
    raise AssertionError


@service_action(ServiceSpec(service=_rename_authors, input_serializer=_RenameIn))
def rename(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...


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
        {
            "queryset": Author.objects.all(),
            "serializer_class": AuthorSerializer,
            "action_specs": {
                "create": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn)
            },
        },
        {"post": "create"},
        {"POST": {"new_name"}},
        id="spec-input-wins-over-serializer-class",
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
        # one body can be described, and picking a variant would be a guess.
        {"action_specs": {"create": _distinct_inputs()}},
        {"post": "create"},
        {"POST": set()},
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
        # A real PUT here never calls ``get_object()``: the core resolves the
        # target through the instance selector. DRF's metadata calls it anyway,
        # as its object-permission probe, so the PUT is left undescribed rather
        # than demanding a ``queryset`` the endpoint does not need.
        {
            "action_specs": {
                "update": ServiceSpec(
                    service=_rename_one,
                    input_serializer=_RenameIn,
                    instance_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_lookup_author
                    ),
                )
            }
        },
        {"put": "update"},
        {},
        id="put-instance-selector-no-queryset",
    ),
    pytest.param(
        # A retrieve selector answers ``get_object()`` before DRF's lookup
        # does, but this PUT does not use it either, so the answer is the same.
        {
            "action_specs": {
                "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup_author),
                "update": ServiceSpec(
                    service=_rename_one,
                    input_serializer=_RenameIn,
                    instance_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_lookup_author
                    ),
                ),
            }
        },
        {"put": "update"},
        {},
        id="put-instance-selector-beside-a-retrieve-selector",
    ),
    pytest.param(
        # A composition carrying no retrieve mixin reaches the same answer.
        {
            "bases": (ServiceUpdateMixin, ActionSerializerResolver, GenericViewSet),
            "action_specs": {
                "update": ServiceSpec(
                    service=_rename_one,
                    input_serializer=_RenameIn,
                    instance_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_lookup_author
                    ),
                )
            },
        },
        {"put": "update"},
        {},
        id="put-instance-selector-without-a-retrieve-mixin",
    ),
    pytest.param(
        # Bulk: no single row to fetch, so the same holds.
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
]


@pytest.mark.django_db
@pytest.mark.parametrize(("attrs", "actions", "described"), _SPEC_ONLY_SHAPES)
def test_options_describes_every_spec_only_shape(
    attrs: dict[str, Any], actions: dict[str, str], described: dict[str, set[str]]
) -> None:
    """Each of these raised DRF's ``serializer_class`` or ``queryset``
    ``AssertionError`` out of the metadata path, so OPTIONS (and every CORS
    preflight reaching the view) ended in a 500."""
    author = Author.objects.create(name="Ada")
    response = _view(attrs).as_view(actions)(factory.options("/"), pk=author.pk)
    assert response.status_code == 200
    assert _described(response) == described


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
    """The PUT is left out only where a real PUT would not call
    ``get_object()``. Elsewhere DRF's object probe still runs: a missing row
    drops the PUT description, as it always has."""
    attrs = {
        "queryset": Author.objects.all(),
        "action_specs": {"update": ServiceSpec(service=_rename_one, input_serializer=_RenameIn)},
    }
    response = _view(attrs).as_view({"put": "update"})(factory.options("/"), pk=0)
    assert response.status_code == 200
    assert _described(response) == {}


@pytest.mark.django_db
def test_outside_options_get_object_looks_up_whatever_the_put_spec() -> None:
    """The guard is for DRF's metadata probe alone. A real request that calls
    ``get_object()`` (a hook reading the row, say) gets the row even when the
    PUT's own spec resolves its target elsewhere."""
    author = Author.objects.create(name="Ada")
    attrs = {
        "queryset": Author.objects.all(),
        "action_specs": {
            "update": ServiceSpec(service=_rename_authors, input_serializer=_RenameIn, many=True)
        },
    }
    view = _view(attrs)(action_map={"put": "update"})
    view.action = "update"
    view.request = factory.put("/")
    view.kwargs = {"pk": author.pk}
    view.format_kwarg = None
    assert view.get_object() == author


def test_outside_options_a_handler_stamped_spec_does_not_pick_the_serializer() -> None:
    """Only the metadata path reads a decorator's spec to pick a serializer.
    Every real action resolves exactly as it did: from ``action_specs``, then
    ``serializer_class``."""

    @selector_action(SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_RenameIn))
    def lookup(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any: ...

    view = _view({"serializer_class": AuthorSerializer, "lookup": lookup})()
    view.action = "lookup"
    assert view.get_serializer_class() is AuthorSerializer
