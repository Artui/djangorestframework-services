"""A call's input serializer is built once, for the call's own body, row and context.

DRF's view builds one serializer for a request, with the request's data, the
row it updates and its context, so a ``get_fields`` or an ``__init__`` reading
``self.initial_data``, ``self.instance`` or ``self.partial`` reads the call's.
The serializer the edge reads the call's fields from is that one: the fields it
describes are the fields it validates with, on HTTP and off it, and the
serializer is built, and queries as it is built, once.
"""

from __future__ import annotations

import collections
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Post, Tag

BUILT: collections.Counter[str] = collections.Counter()


class _Counted(serializers.Serializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        BUILT[type(self).__name__] += 1
        super().__init__(*args, **kwargs)


class InitialDataKind(_Counted):
    """A field the body's own ``kind`` adds, read off ``initial_data`` as the fields are built."""

    kind = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        if self.initial_data.get("kind") == "long":
            fields["extra"] = serializers.CharField()
        return fields


class InitialDataInInit(_Counted):
    """``initial_data`` read as the serializer is built."""

    kind = serializers.CharField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kind_sent = self.initial_data["kind"]


class InstancePk(_Counted):
    """The row's ``pk`` read as the fields are built."""

    title = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["title"].help_text = f"for {self.instance.pk}"
        return fields


class InstanceAddsField(_Counted):
    """A field only an update takes."""

    title = serializers.CharField(required=False)

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        if self.instance is not None:
            fields["reason"] = serializers.CharField()
        return fields


class InstanceMakesReadOnly(_Counted):
    """A field an update never writes."""

    title = serializers.CharField()
    body = serializers.CharField(required=False)

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        if self.instance is not None:
            fields["title"] = serializers.CharField(read_only=True)
        return fields


class PartialAddsField(_Counted):
    """A field only a partial call takes."""

    title = serializers.CharField(required=False)

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        if self.partial:
            fields["note"] = serializers.CharField()
        return fields


class Row(_Counted):
    kind = serializers.CharField()


class QueriesAsBuilt(_Counted):
    """One query each time the serializer is built."""

    title = serializers.CharField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        list(Tag.objects.all()[:1])


class LimitFromContext(_Counted):
    """A bound read off the context, tolerant of a context that lacks it."""

    title = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        limit = self.context.get("limit")
        if limit is not None:
            fields["title"] = serializers.CharField(max_length=limit)
        return fields


def _created(*, data: Any) -> dict[str, Any]:
    return {"got": dict(data)}


def _updated(*, instance: Post, data: Any) -> dict[str, Any]:
    return {"got": dict(data), "pk": instance.pk}


def _spec(serializer: type[serializers.Serializer], **options: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_updated if options.pop("update", False) else _created,
        input_serializer=serializer,
        permission_classes=[],
        atomic=False,
        **options,
    )


def _many_spec() -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=lambda *, data: [dict(row) for row in data],
        input_serializer=Row,
        many=True,
        permission_classes=[],
        atomic=False,
    )


_factory = APIRequestFactory()


def _create(spec: ServiceSpec[Any, Any, Any], body: Any, **request: Any) -> Any:
    view = type(
        "_View",
        (ServiceCreateView,),
        {"spec": spec, "permission_classes": [], "authentication_classes": []},
    ).as_view()
    return view(_factory.post("/", body, **({"format": "json"} | request)))


def _update(spec: ServiceSpec[Any, Any, Any], body: Any, post: Post) -> Any:
    view = type(
        "_View",
        (ServiceUpdateView,),
        {
            "spec": spec,
            "permission_classes": [],
            "authentication_classes": [],
            "queryset": Post.objects.all(),
        },
    ).as_view()
    return view(_factory.patch(f"/{post.pk}/", body, format="json"), pk=post.pk)


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@pytest.fixture
def post(db: Any) -> Post:
    BUILT.clear()
    return Post.objects.create(title="t")


@pytest.fixture(autouse=True)
def _counted() -> None:
    BUILT.clear()


class TestOverALiveView:
    def test_a_field_the_body_adds_validates(self, db: Any) -> None:
        response = _create(_spec(InitialDataKind), {"kind": "long", "extra": "x"})

        assert response.status_code == 201, response.data
        assert response.data == {"got": {"kind": "long", "extra": "x"}}

    def test_a_serializer_reading_its_body_as_it_is_built_validates(self, db: Any) -> None:
        response = _create(_spec(InitialDataInInit), {"kind": "k"})

        assert response.status_code == 201, response.data

    def test_a_serializer_reading_its_row_validates(self, post: Post) -> None:
        response = _update(_spec(InstancePk, update=True), {"title": "n"}, post)

        assert response.status_code == 200, response.data
        assert response.data == {"got": {"title": "n"}, "pk": post.pk}

    def test_a_field_only_an_update_takes_validates(self, post: Post) -> None:
        response = _update(_spec(InstanceAddsField, update=True), {"reason": "why"}, post)

        assert response.status_code == 200, response.data
        assert response.data["got"] == {"reason": "why"}

    def test_a_field_an_update_never_writes_is_not_required(self, post: Post) -> None:
        response = _update(_spec(InstanceMakesReadOnly, update=True), {"body": "b"}, post)

        assert response.status_code == 200, response.data
        assert response.data["got"] == {"body": "b"}

    def test_a_field_only_a_partial_call_takes_validates(self, post: Post) -> None:
        response = _update(_spec(PartialAddsField, update=True), {"note": "n"}, post)

        assert response.status_code == 200, response.data
        assert response.data["got"] == {"note": "n"}

    def test_a_form_body_is_the_body_the_fields_are_built_for(self, db: Any) -> None:
        response = _create(
            _spec(InitialDataKind), {"kind": "long", "extra": "x"}, format="multipart"
        )

        assert response.status_code == 201, response.data
        assert response.data == {"got": {"kind": "long", "extra": "x"}}
        assert BUILT == {"InitialDataKind": 1}


@_CORES
class TestOffHttp:
    def test_a_field_the_body_adds_validates(self, dispatch: Callable[..., DispatchResult]) -> None:
        result = dispatch(_spec(InitialDataKind), params={"kind": "long", "extra": "x"})

        assert result.value == {"got": {"kind": "long", "extra": "x"}}

    def test_a_serializer_reading_its_body_as_it_is_built_validates(
        self, dispatch: Callable[..., DispatchResult]
    ) -> None:
        assert dispatch(_spec(InitialDataInInit), params={"kind": "k"}).value == {
            "got": {"kind": "k"}
        }

    def test_a_serializer_reading_its_row_validates(
        self, dispatch: Callable[..., DispatchResult], post: Post
    ) -> None:
        result = dispatch(_spec(InstancePk, update=True), params={"title": "n"}, instance=post)

        assert result.value == {"got": {"title": "n"}, "pk": post.pk}

    @pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
    def test_a_field_only_an_update_takes_is_no_unexpected_argument(
        self, dispatch: Callable[..., DispatchResult], post: Post, policy: UnknownArguments
    ) -> None:
        result = dispatch(
            _spec(InstanceAddsField, update=True),
            params={"reason": "why"},
            instance=post,
            unknown_arguments=policy,
        )

        assert result.value["got"] == {"reason": "why"}

    def test_a_field_an_update_never_writes_is_not_required(
        self, dispatch: Callable[..., DispatchResult], post: Post
    ) -> None:
        result = dispatch(
            _spec(InstanceMakesReadOnly, update=True), params={"body": "b"}, instance=post
        )

        assert result.value["got"] == {"body": "b"}

    def test_a_field_only_a_partial_call_takes_is_no_unexpected_argument(
        self, dispatch: Callable[..., DispatchResult], post: Post
    ) -> None:
        result = dispatch(
            _spec(PartialAddsField, update=True),
            params={"note": "n"},
            instance=post,
            partial=True,
            unknown_arguments=UnknownArguments.REJECT,
        )

        assert result.value["got"] == {"note": "n"}


class TestBuiltOnce:
    @_CORES
    @pytest.mark.parametrize("update", [False, True], ids=["create", "update"])
    def test_off_http(
        self, dispatch: Callable[..., DispatchResult], post: Post, update: bool
    ) -> None:
        extra = {"instance": post} if update else {}

        dispatch(_spec(InitialDataKind, update=update), params={"kind": "k"}, **extra)

        assert BUILT == {"InitialDataKind": 1}

    def test_over_a_live_view(self, post: Post) -> None:
        _create(_spec(InitialDataKind), {"kind": "k"})
        _update(_spec(InstancePk, update=True), {"title": "n"}, post)

        assert BUILT == {"InitialDataKind": 1, "InstancePk": 1}

    @_CORES
    def test_a_many_call(self, dispatch: Callable[..., DispatchResult], db: Any) -> None:
        """Compiling a ``many`` spec reads its list serializer's bounds, once per spec."""
        spec = _many_spec()
        rows = [{"kind": "a"}, {"kind": "b"}]
        dispatch(spec, params=rows)

        BUILT.clear()
        dispatch(spec, params=rows)

        assert BUILT == {"Row": 1}

    def test_a_many_call_over_a_live_view(self, db: Any) -> None:
        spec = _many_spec()
        rows = [{"kind": "a"}, {"kind": "b"}]
        _create(spec, rows)

        BUILT.clear()
        response = _create(spec, rows)

        assert response.status_code == 201, response.data
        assert BUILT == {"Row": 1}

    @_CORES
    def test_its_queries_run_once_off_http(
        self,
        dispatch: Callable[..., DispatchResult],
        db: Any,
        django_assert_num_queries: Any,
    ) -> None:
        spec = _spec(QueriesAsBuilt)

        with django_assert_num_queries(1):
            dispatch(spec, params={"title": "a"})

    def test_its_queries_run_once_over_a_live_view(
        self, db: Any, django_assert_num_queries: Any
    ) -> None:
        spec = _spec(QueriesAsBuilt)

        with django_assert_num_queries(1):
            response = _create(spec, {"title": "a"})

        assert response.status_code == 201, response.data


class TestAContextCallableTheDescriptionCannotAsk:
    """A callable taking ``**kwargs`` is asked by the serializer that validates alone.

    The description holds none of the call's seeds, so it reads the fields
    without the callable's answer, and the serializer that validates is built
    with it: the limit the answer sets is the one the body is refused by.
    """

    def _spec(self) -> ServiceSpec[Any, Any, Any]:
        return _spec(LimitFromContext, input_serializer_context=lambda **_: {"limit": 2})

    def test_over_a_live_view(self, db: Any) -> None:
        response = _create(self._spec(), {"title": "abc"})

        assert response.status_code == 400
        assert "title" in response.data

    @_CORES
    def test_off_http(self, dispatch: Callable[..., DispatchResult], db: Any) -> None:
        with pytest.raises(ValidationError) as caught:
            dispatch(self._spec(), params={"title": "abc"})

        assert "title" in caught.value.detail


@_CORES
class TestDescribedForTheBodyAndRowItValidates:
    """A body the server put a value into, and a row a lookup found, are the ones described.

    The call is described for the caller's body before the server's values
    are known, and for no row before its lookup runs; the kernel's check of
    the arguments and the Validator read the serializer built for what the
    call validates.
    """

    def test_a_field_the_server_s_value_adds_is_the_call_s(
        self, dispatch: Callable[..., DispatchResult], db: Any
    ) -> None:
        spec = _spec(InitialDataKind, input_data=lambda: {"kind": "long"})

        result = dispatch(
            spec,
            params={"kind": "short", "extra": "x"},
            unknown_arguments=UnknownArguments.REJECT,
        )

        assert result.value == {"got": {"kind": "long", "extra": "x"}}

    def test_a_row_a_lookup_resolves_is_the_one_described(
        self, dispatch: Callable[..., DispatchResult], post: Post
    ) -> None:
        spec = _spec(
            InstanceMakesReadOnly,
            update=True,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda pk: Post.objects.filter(pk=pk)
            ),
        )

        result = dispatch(spec, params={"pk": post.pk, "body": "b"})

        assert result.value == {"got": {"body": "b"}, "pk": post.pk}


@_CORES
class TestBuiltTwice:
    """A body the server puts a value into, and a row a lookup resolves, are built for twice.

    Once for what the call is described with before it knows them, once for
    what it validates; the context callable is asked for each where what it
    is handed differs.
    """

    def test_a_body_the_server_puts_a_value_into(
        self, dispatch: Callable[..., DispatchResult], db: Any
    ) -> None:
        asked: list[Any] = []

        def context(request: Any) -> dict[str, Any]:
            asked.append(request.data)
            return {}

        spec = _spec(
            InitialDataKind,
            input_data=lambda: {"kind": "long"},
            input_serializer_context=context,
        )

        dispatch(spec, params={"kind": "k", "extra": "x"})

        assert BUILT == {"InitialDataKind": 2}
        # Off HTTP with no view the request is built for the body it carries.
        assert asked == [{"kind": "k", "extra": "x"}, {"kind": "long", "extra": "x"}]

    def test_a_row_a_lookup_resolves(
        self, dispatch: Callable[..., DispatchResult], post: Post
    ) -> None:
        spec = _spec(
            InitialDataKind,
            update=True,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda pk: Post.objects.filter(pk=pk)
            ),
        )

        dispatch(spec, params={"pk": post.pk, "kind": "k"})

        assert BUILT == {"InitialDataKind": 2}
