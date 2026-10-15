"""A target lookup's filter named like a writable input field is refused at ``as_view()``.

A filter's value comes off the query string and a field's off the body, and one
argument name cannot be both, so dispatch refuses the spec on every request.
Where the input serializer can be read with no request, the refusal comes when
the view is wired instead, as every other declaration that can never run is;
where it cannot, the request's refusal stays.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    service_action,
)
from tests.testapp.models import Post

factory = APIRequestFactory()


class _ByTitle(django_filters.FilterSet):
    class Meta:
        model = Post
        fields = ["title"]


class _Title(serializers.Serializer):
    title = serializers.CharField()


class _ReadOnlyTitle(serializers.Serializer):
    title = serializers.CharField(read_only=True)
    body = serializers.CharField()


class _HiddenTitle(serializers.Serializer):
    title = serializers.HiddenField(default="hidden")
    body = serializers.CharField()


class _TitleIsTheSource(serializers.Serializer):
    heading = serializers.CharField(source="title")


class _TitleOnTheWire(serializers.Serializer):
    title = serializers.CharField(source="body")


class _NeedsTheRequest(serializers.Serializer):
    """Built only with a request in its context, as a view builds it."""

    title = serializers.CharField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context["request"].method  # noqa: B018 - the read is the point


def _by_pk(*, pk: Any) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _all() -> QuerySet[Post]:
    return Post.objects.all()


def _service(**kwargs: Any) -> None:
    return None


def _instance_lookup() -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk, filter_set=_ByTitle)


def _collection_lookup() -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.LIST, selector=_all, filter_set=_ByTitle)


@pytest.mark.parametrize("base", [ServiceUpdateView, ServiceDeleteView], ids=["update", "delete"])
def test_an_instance_lookup_s_filter_is_refused_at_as_view(base: type) -> None:
    view = type(
        "_View",
        (base,),
        {
            "spec": ServiceSpec(
                service=_service, input_serializer=_Title, instance_selector_spec=_instance_lookup()
            )
        },
    )

    with pytest.raises(
        ImproperlyConfigured,
        match=r"_View\.spec\.instance_selector_spec\.filter_set _ByTitle declares the filter "
        r"'title', which the input serializer _Title also takes as a writable field",
    ):
        view.as_view()


def test_a_collection_lookup_s_filter_is_refused_at_as_view() -> None:
    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_service, input_serializer=_Title, collection_selector_spec=_collection_lookup()
        )

    with pytest.raises(
        ImproperlyConfigured,
        match=r"_View\.spec\.collection_selector_spec\.filter_set _ByTitle declares the filter "
        r"'title', which the input serializer _Title also takes as a writable field",
    ):
        _View.as_view()


def test_a_viewset_action_s_filter_is_refused_at_as_view() -> None:
    class _ViewSet(ServiceViewSet):
        queryset = Post.objects.all()
        action_specs = {  # noqa: RUF012 - the viewset's declaration
            "partial_update": ServiceSpec(
                service=_service, input_serializer=_Title, instance_selector_spec=_instance_lookup()
            )
        }

    with pytest.raises(
        ImproperlyConfigured,
        match=r"_ViewSet\.action_specs\['partial_update'\]\.instance_selector_spec\.filter_set",
    ):
        _ViewSet.as_view({"patch": "partial_update"})


def test_a_service_action_s_filter_is_refused_where_its_spec_is_checked() -> None:
    """``@service_action`` checks its spec when it decorates, as it checks the rest."""
    with pytest.raises(ImproperlyConfigured, match=r"instance_selector_spec\.filter_set _ByTitle"):
        service_action(
            spec=ServiceSpec(
                service=_service, input_serializer=_Title, instance_selector_spec=_instance_lookup()
            ),
            detail=True,
            methods=["patch"],
        )(lambda self, request, pk=None: None)


@pytest.mark.parametrize(
    "serializer",
    [_ReadOnlyTitle, _HiddenTitle, _TitleIsTheSource],
    ids=["read-only-field", "hidden-field", "the-field-s-source"],
)
@pytest.mark.django_db
def test_a_name_the_body_does_not_write_is_accepted(serializer: type) -> None:
    """The names the body writes, as the client sends them, as dispatch reads them:
    a read-only field and a ``HiddenField`` write nothing, and a field's ``source``
    is not the name it arrives under."""

    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=_service, input_serializer=serializer, instance_selector_spec=_instance_lookup()
        )

    post = Post.objects.create(title="a")
    response = _View.as_view()(
        factory.patch("/?title=a", {"body": "x", "heading": "y"}, format="json"), pk=post.pk
    )

    assert response.status_code == 204


def test_the_wire_name_is_the_one_refused() -> None:
    """A field arriving as ``title`` is refused whatever attribute it writes."""

    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=_service,
            input_serializer=_TitleOnTheWire,
            instance_selector_spec=_instance_lookup(),
        )

    with pytest.raises(ImproperlyConfigured, match="the input serializer _TitleOnTheWire"):
        _View.as_view()


@pytest.mark.django_db
def test_a_serializer_unreadable_without_a_request_is_refused_by_the_request() -> None:
    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=_service,
            input_serializer=_NeedsTheRequest,
            instance_selector_spec=_instance_lookup(),
        )

    handler = _View.as_view()
    post = Post.objects.create(title="a")

    with pytest.raises(ImproperlyConfigured, match="declared by two sources"):
        handler(factory.patch("/?title=a", {"title": "x"}, format="json"), pk=post.pk)


def test_a_many_spec_s_fields_are_each_row_s() -> None:
    """A ``many`` spec declares no target lookup, so there is no filter to clash;
    the spec itself is refused for the lookup, as it always was."""
    with pytest.raises(ImproperlyConfigured, match="instance_selector_spec with many=True"):
        ServiceSpec(
            service=_service,
            input_serializer=_Title,
            many=True,
            instance_selector_spec=_instance_lookup(),
        )


class _FailsToBuild(serializers.Serializer):
    title = serializers.CharField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ValueError("not at import")


def test_a_lookup_with_no_filter_set_leaves_the_serializer_unbuilt() -> None:
    """Nothing can clash, so ``as_view()`` builds no serializer to find out, and
    one that fails to build fails where it did, at the request."""

    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=_service,
            input_serializer=_FailsToBuild,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
        )

    _View.as_view()
