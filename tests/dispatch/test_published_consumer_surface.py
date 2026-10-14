"""What the published transports import from this package and pass to ``dispatch_spec``.

The published djangorestframework-mcp-server 0.52.0 and
djangorestframework-pydantic-ai 0.35.0 floor this package unbounded, so they
resolve onto this release as they are. Every name either imports is listed here,
read off their import statements at those tags, the two private ones in
``dispatch.utils`` included, and every ``dispatch_spec`` keyword either passes is
passed. A name or a keyword leaving fails here before it fails in a transport.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
)
from rest_framework_services.dispatch.utils import (
    declared_input_keys,
    resolve_unknown_arguments,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.url_kwarg import UrlKwarg
from tests.testapp.models import Author, Post

# ``(module, name)`` for each ``from <module> import <name>`` at the two tags.
PUBLISHED_IMPORTS: list[tuple[str, str]] = [
    ("rest_framework_services", "ActionUnavailable"),
    ("rest_framework_services", "AdditionalInputRequired"),
    ("rest_framework_services", "Affordance"),
    ("rest_framework_services", "ArgumentBinding"),
    ("rest_framework_services", "AudienceProjection"),
    ("rest_framework_services", "DEFAULT_JSON_SCHEMA_REGISTRY"),
    ("rest_framework_services", "DEFAULT_PAGE_SIZE"),
    ("rest_framework_services", "DEFAULT_POOL_SEEDS"),
    ("rest_framework_services", "DispatchResult"),
    ("rest_framework_services", "FieldAudience"),
    ("rest_framework_services", "FieldMarking"),
    ("rest_framework_services", "JsonSchemaRegistry"),
    ("rest_framework_services", "OfflineContext"),
    ("rest_framework_services", "OfflineContract"),
    ("rest_framework_services", "OfflineServiceView"),
    ("rest_framework_services", "OutputPage"),
    ("rest_framework_services", "PoolSeeds"),
    ("rest_framework_services", "SelectorKind"),
    ("rest_framework_services", "SelectorSpec"),
    ("rest_framework_services", "ServiceError"),
    ("rest_framework_services", "ServiceSpec"),
    ("rest_framework_services", "ServiceValidationError"),
    ("rest_framework_services", "SpecRegistry"),
    ("rest_framework_services", "UNSET"),
    ("rest_framework_services", "UnknownArguments"),
    ("rest_framework_services", "UnsetType"),
    ("rest_framework_services", "adispatch_spec"),
    ("rest_framework_services", "arun_selector"),
    ("rest_framework_services", "audience_projection_for_spec"),
    ("rest_framework_services", "base_pool"),
    ("rest_framework_services", "base_serializer_context"),
    ("rest_framework_services", "build_audience_projection"),
    ("rest_framework_services", "build_offline_context"),
    ("rest_framework_services", "can_present_nothing"),
    ("rest_framework_services", "dispatch_spec"),
    ("rest_framework_services", "enforce_affordances"),
    ("rest_framework_services", "enforce_permissions"),
    ("rest_framework_services", "is_async"),
    ("rest_framework_services", "is_queryset"),
    ("rest_framework_services", "materialize_retrieve"),
    ("rest_framework_services", "operation_affordances"),
    ("rest_framework_services", "output_to_json_schema"),
    ("rest_framework_services", "paginate_output"),
    ("rest_framework_services", "provider_keys"),
    ("rest_framework_services", "render_for_audience"),
    ("rest_framework_services", "renderable_serializer_class"),
    ("rest_framework_services", "resolve_callable_kwargs"),
    ("rest_framework_services", "run_selector"),
    ("rest_framework_services", "run_service"),
    ("rest_framework_services", "serializer_to_json_schema"),
    ("rest_framework_services", "server_owned_keys"),
    ("rest_framework_services", "spec_to_json_schema"),
    ("rest_framework_services", "unmet_operation_affordance"),
    ("rest_framework_services.dispatch.unguarded_specs", "unguarded_specs"),
    ("rest_framework_services.dispatch.utils", "declared_input_keys"),
    ("rest_framework_services.dispatch.utils", "resolve_unknown_arguments"),
    ("rest_framework_services.exceptions.action_unavailable", "ActionUnavailable"),
    ("rest_framework_services.exceptions.additional_input_required", "AdditionalInputRequired"),
    ("rest_framework_services.exceptions.service_error", "ServiceError"),
    ("rest_framework_services.exceptions.service_validation_error", "ServiceValidationError"),
    ("rest_framework_services.registry.spec_registry", "SpecRegistry"),
    ("rest_framework_services.types.affordance", "Affordance"),
    ("rest_framework_services.types.audience_projection", "AudienceProjection"),
    ("rest_framework_services.types.dispatch_result", "DispatchResult"),
    ("rest_framework_services.types.field_audience", "FieldAudience"),
    ("rest_framework_services.types.offline_context", "OfflineContext"),
    ("rest_framework_services.types.pool_seeds", "DEFAULT_POOL_SEEDS"),
    ("rest_framework_services.types.pool_seeds", "PoolSeeds"),
    ("rest_framework_services.types.progress_reporter", "ProgressReporter"),
    ("rest_framework_services.types.query_param", "QueryParam"),
    ("rest_framework_services.types.reserved_pool_seeds", "RESERVED_POOL_SEEDS"),
    ("rest_framework_services.types.selector_kind", "SelectorKind"),
    ("rest_framework_services.types.selector_spec", "SelectorSpec"),
    ("rest_framework_services.types.service_spec", "ServiceSpec"),
    ("rest_framework_services.types.url_kwarg", "UrlKwarg"),
    ("rest_framework_services.types.validate_channel_names", "validate_channel_names"),
]


@pytest.mark.parametrize(("module", "name"), PUBLISHED_IMPORTS)
def test_every_published_import_resolves(module: str, name: str) -> None:
    assert hasattr(importlib.import_module(module), name)


def test_passthrough_stays_a_member_a_transport_compares_by_identity() -> None:
    # drf-mcp 0.52.0's own unknown-argument handling, configured with IGNORE.
    unknown_arguments = UnknownArguments.IGNORE
    assert (unknown_arguments is UnknownArguments.PASSTHROUGH) is False


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _posts_by(*, author_pk: int) -> QuerySet[Post]:
    return Post.objects.filter(author_id=author_pk).order_by("pk")


def _create(*, data: Any) -> Post:
    return Post.objects.create(**data)


@pytest.mark.django_db
class TestTheKeywordsTheTransportsPass:
    def test_drf_mcp_hands_a_declared_url_kwarg_on_the_offline_view(self) -> None:
        author = Author.objects.create(name="a")
        Post.objects.create(title="a", author=author)
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_posts_by,
            reads=[UrlKwarg("author_pk", type="integer", required=True)],
            permission_classes=[],
        )
        # drf-mcp splits the declared UrlKwarg off the arguments, onto the view.
        offline = build_offline_context(None, kwargs={"author_pk": author.pk})
        enforce_permissions(spec, offline)
        result = dispatch_spec(
            spec,
            user=None,
            params={},
            request=offline.request,
            view=offline.view,
            argument_binding=ArgumentBinding.AUTO,
            unknown_arguments=UnknownArguments.REJECT,
            on_target_resolved=enforce_permissions,
            progress=None,
            pool_seeds=DEFAULT_POOL_SEEDS,
            many_as_argument=True,
        )
        assert [post.title for post in result.value] == ["a"]

    def test_pai_passes_filter_data_and_many_as_argument(self) -> None:
        spec = ServiceSpec(service=_create, input_serializer=_TitleIn, permission_classes=[])
        offline = build_offline_context(None, {"title": "b"})
        enforce_permissions(spec, offline)
        result = dispatch_spec(
            spec,
            user=None,
            params={"title": "b"},
            request=offline.request,
            view=offline.view,
            unknown_arguments=UnknownArguments.IGNORE,
            argument_binding=ArgumentBinding.AUTO,
            on_target_resolved=enforce_permissions,
            progress=None,
            filter_data=None,
            many_as_argument=True,
        )
        assert result.value.title == "b"

    def test_the_two_private_names_answer_as_drf_mcp_calls_them(self) -> None:
        spec = ServiceSpec(service=_create, input_serializer=_TitleIn, permission_classes=[])
        declared_input_keys(spec, serializer=None, argument_binding=ArgumentBinding.AUTO)
        resolve_unknown_arguments(
            spec,
            {},
            unknown_arguments=UnknownArguments.REJECT,
            serializer=None,
            argument_binding=ArgumentBinding.AUTO,
        )


class TestRetiredModesAreRefused:
    def test_passthrough(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_by, permission_classes=[])
        with pytest.raises(ValueError, match="PASSTHROUGH"):
            dispatch_spec(
                spec, user=None, params={}, unknown_arguments=UnknownArguments.PASSTHROUGH
            )

    def test_spread_caller_wins(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_posts_by, permission_classes=[])
        with pytest.raises(ValueError, match="SPREAD_CALLER_WINS"):
            dispatch_spec(
                spec,
                user=None,
                params={},
                argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
            )
