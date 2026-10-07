"""One annotation that does not resolve costs only its own name.

A name imported under ``if TYPE_CHECKING:`` is routine, and under ``from __future__
import annotations`` an annotation naming one cannot be resolved at runtime. The
markers on every *other* parameter of the callable, and on every other key of a
``TypedDict``, are read as though it were not there: a ``NotClientInput`` key is
hidden and stripped, and an ``InputRequired`` key is required, under every policy
and on both cores. The unresolved annotation's own markers are read too, by
evaluating it with what does not resolve standing in for ``Any``, and failing
that, by name.

Every test drives a signature that existed before the fix (``spec_to_json_schema``,
``dispatch_spec``, ``adispatch_spec``, ``marked_input_keys``, ``as_view()``), so
each fails rather than errors against a tree without it.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Annotated, Any, Optional

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import ValidationError
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services import (
    DispatchResult,
    InputDescription,
    InputRequired,
    NotClientInput,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
    spec_to_json_schema,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.marked_input_keys import marked_input_keys

if TYPE_CHECKING:
    # Only a type checker sees these, so the runtime cannot resolve an annotation
    # that names one: the routine postponed-annotations idiom.
    from tests.testapp import models as _models
    from tests.testapp.models import Post as _Owner


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_NOTHING: frozenset[str] = frozenset()


def _input_schema(selector: Any) -> dict[str, Any]:
    return spec_to_json_schema(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector))


# ------------------------------------------------- a marker beside an unresolved name


def _hidden(
    *,
    team: Annotated[str, NotClientInput] = "own-team",
    owner: _Owner | None = None,
    limit: int = 10,
) -> str:
    return team


def _hidden_open(
    *,
    team: Annotated[str, NotClientInput] = "own-team",
    owner: _Owner | None = None,
    **filters: Any,
) -> str:
    return team


class _HiddenKey(TypedDict, total=False):
    team: Annotated[str, NotClientInput]
    owner: _Owner
    limit: int


def _hidden_key(**extras: Unpack[_HiddenKey]) -> str:
    return extras.get("team", "own-team")


def _required(*, limit: Annotated[int, InputRequired] = 10, owner: _Owner | None = None) -> int:
    return limit


class _RequiredKey(TypedDict, total=False):
    limit: Annotated[int, InputRequired]
    owner: _Owner


def _required_key(**extras: Unpack[_RequiredKey]) -> int:
    return extras["limit"]


class TestNotClientInputBesideAnUnresolvedName:
    @pytest.mark.parametrize("selector", [_hidden, _hidden_key], ids=["parameter", "typed-dict"])
    def test_the_key_is_not_advertised(self, selector: Any) -> None:
        # ``limit`` typed shows the reflection read the hints rather than giving up.
        properties = _input_schema(selector)["properties"]
        assert "team" not in properties
        assert properties["limit"] == {"type": "integer"}

    @_CORES
    @pytest.mark.parametrize(
        ("selector", "policy"),
        [
            pytest.param(_hidden, UnknownArguments.IGNORE, id="closed-ignore"),
            pytest.param(_hidden, UnknownArguments.PASSTHROUGH, id="closed-passthrough"),
            pytest.param(_hidden_open, UnknownArguments.REJECT, id="open-reject"),
            pytest.param(_hidden_open, UnknownArguments.IGNORE, id="open-ignore"),
            pytest.param(_hidden_open, UnknownArguments.PASSTHROUGH, id="open-passthrough"),
            pytest.param(_hidden_key, UnknownArguments.IGNORE, id="typed-dict-ignore"),
            pytest.param(_hidden_key, UnknownArguments.PASSTHROUGH, id="typed-dict-passthrough"),
        ],
    )
    def test_the_callers_value_never_reaches_the_selector(
        self, dispatch: Dispatch, selector: Any, policy: UnknownArguments
    ) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)

        result = dispatch(spec, params={"team": "other-team"}, unknown_arguments=policy)

        assert result.value == "own-team"

    @_CORES
    @pytest.mark.parametrize("selector", [_hidden, _hidden_key], ids=["parameter", "typed-dict"])
    def test_reject_refuses_it_on_a_closed_selector(
        self, dispatch: Dispatch, selector: Any
    ) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)

        with pytest.raises(ValidationError) as excinfo:
            dispatch(spec, params={"team": "other-team"}, unknown_arguments=UnknownArguments.REJECT)

        assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'team'."]}


@pytest.mark.parametrize("selector", [_required, _required_key], ids=["parameter", "typed-dict"])
class TestInputRequiredBesideAnUnresolvedName:
    def test_the_key_is_listed_as_required(self, selector: Any) -> None:
        schema = _input_schema(selector)
        assert schema.get("required") == ["limit"]
        assert schema["properties"]["limit"] == {"type": "integer"}

    @_CORES
    def test_a_call_without_it_is_refused(self, dispatch: Dispatch, selector: Any) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)

        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch(spec, params={})

        assert excinfo.value.detail == {
            "non_field_errors": ["Missing required argument(s): 'limit'."]
        }


class _Tags(TypedDict, total=False):
    tag: str


def _tagged(*, owner: _Owner | None = None, **extras: Unpack[_Tags]) -> Any:
    return extras.get("tag")


@_CORES
def test_reject_reads_an_unpacked_typed_dict_beside_an_unresolved_name(dispatch: Dispatch) -> None:
    """Only the ``**extras`` annotation decides the surface, so ``owner`` not
    resolving no longer makes ``REJECT`` refuse to run on an unknown one."""
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_tagged)

    assert (
        dispatch(spec, params={"tag": "x"}, unknown_arguments=UnknownArguments.REJECT).value == "x"
    )
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"colour": "red"}, unknown_arguments=UnknownArguments.REJECT)
    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'colour'."]}


if TYPE_CHECKING:

    class _UnknowableExtras(TypedDict, total=False):
        team: Annotated[str, NotClientInput]


def _unknowable(**extras: Unpack[_UnknowableExtras]) -> Any:
    return extras.get("team", "own-team")


_UNKNOWABLE = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_unknowable)


@_CORES
@pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH])
def test_an_unpacked_typed_dict_that_does_not_resolve_hides_nothing(
    dispatch: Dispatch, policy: UnknownArguments
) -> None:
    """The one thing that stays unknowable: nothing says which keys it has, so none
    of them is read as hidden, and the permissive policies deliver what was sent."""
    result = dispatch(_UNKNOWABLE, params={"team": "other-team"}, unknown_arguments=policy)

    assert result.value == "other-team"


@_CORES
def test_reject_refuses_to_run_on_an_unpacked_typed_dict_that_does_not_resolve(
    dispatch: Dispatch,
) -> None:
    with pytest.raises(ImproperlyConfigured, match="REJECT cannot be enforced"):
        dispatch(
            _UNKNOWABLE, params={"team": "other-team"}, unknown_arguments=UnknownArguments.REJECT
        )


# ------------------------------------------------- a marker on the unresolved name itself


def _marked_unresolved(
    *,
    team: Annotated[_Owner, NotClientInput] | None = None,
    limit: Annotated[_Owner | None, InputRequired] = None,
    note: Annotated[list[_Owner], InputDescription("The owners.")] | None = None,
) -> Any:
    return team


class _MarkedUnresolvedKey(TypedDict):
    team: NotRequired[Annotated[_Owner, NotClientInput]]
    note: NotRequired[_Owner]
    limit: Annotated[_Owner, InputRequired]


def _marked_unresolved_key(**extras: Unpack[_MarkedUnresolvedKey]) -> None: ...


class TestMarkersOnTheUnresolvedAnnotation:
    """What does not resolve stands in for ``Any``, so the rest of the annotation,
    the markers included, is read as written. Each spelling here has a layer around
    the unresolved name, which the standard library's forward-reference evaluation
    gives up on before 3.14 (and on ``| None`` and ``NotRequired`` at 3.14)."""

    @pytest.mark.parametrize(
        "selector", [_marked_unresolved, _marked_unresolved_key], ids=["parameter", "typed-dict"]
    )
    def test_its_markers_are_read(self, selector: Any) -> None:
        assert marked_input_keys(selector) == (frozenset({"limit"}), frozenset({"team"}))

    def test_its_schema_keeps_what_it_says(self) -> None:
        schema = _input_schema(_marked_unresolved)
        assert schema["properties"] == {
            "limit": {"anyOf": [{}, {"type": "null"}]},
            "note": {
                "anyOf": [{"type": "array", "items": {}}, {"type": "null"}],
                "description": "The owners.",
            },
        }
        assert schema["required"] == ["limit"]

    def test_a_not_required_wrapper_is_read(self) -> None:
        # Under postponed annotations ``__required_keys__`` cannot see through
        # ``NotRequired``, so only reading the key's annotation demotes ``note``.
        schema = _input_schema(_marked_unresolved_key)
        assert schema["required"] == ["limit"]
        assert set(schema["properties"]) == {"note", "limit"}

    @_CORES
    def test_dispatch_strips_and_requires_it(self, dispatch: Dispatch) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_marked_unresolved)

        assert dispatch(spec, params={"team": "other", "limit": 1}).value is None
        with pytest.raises(ServiceValidationError) as excinfo:
            dispatch(spec, params={})
        assert excinfo.value.detail == {
            "non_field_errors": ["Missing required argument(s): 'limit'."]
        }


# ------------------------------------------------- read by name, as a last resort


def _by_name(
    *,
    team: Annotated[_models.Post, NotClientInput] | None = None,
    limit: Annotated[_models.Post, InputRequired] | None = None,
    nested: Optional["Annotated[_Owner, NotClientInput]"] = None,  # noqa: UP037, UP045 -- the spelling under test
    quoted: "Annotated[_Owner, NotClientInput]" = None,  # noqa: UP037 -- the spelling under test
    plain: _models.Post | None = None,
    misplaced: list[Annotated[_models.Post, NotClientInput]] | None = None,
    described: Annotated[_models.Post, InputRequired, InputDescription("The owner.")] | None = None,
) -> None: ...


def _by_name_contradiction(
    *, team: Annotated[_models.Post, InputRequired, NotClientInput]
) -> None: ...


class TestMarkersReadByName:
    """Where even the stand-in evaluation cannot reach a marker, its name decides.

    ``_models.Post`` asks an attribute of the stand-in, which has none; a marker
    inside a forward reference the evaluation leaves in place is only text. Either
    way the marker is honoured by name, which fails closed: a key whose annotation
    names ``NotClientInput`` is hidden, and one naming ``InputRequired`` required.
    Text cannot place a marker, so ``misplaced`` is hidden rather than refused, and
    it cannot read an ``InputDescription``, so ``described`` publishes none.
    """

    def test_a_named_marker_is_honoured(self) -> None:
        assert marked_input_keys(_by_name) == (
            frozenset({"limit", "described"}),
            frozenset({"team", "nested", "quoted", "misplaced"}),
        )

    def test_the_schema_hides_and_requires_by_name(self) -> None:
        schema = _input_schema(_by_name)
        assert schema["properties"] == {"limit": {}, "plain": {}, "described": {}}
        assert schema["required"] == ["limit", "described"]

    @_CORES
    def test_dispatch_strips_by_name(self, dispatch: Dispatch) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_name)

        with pytest.raises(ValidationError) as excinfo:
            dispatch(
                spec,
                params={
                    "team": "a",
                    "nested": "b",
                    "quoted": "c",
                    "misplaced": [],
                    "limit": 1,
                    "described": 2,
                },
                unknown_arguments=UnknownArguments.REJECT,
            )

        assert excinfo.value.detail == {
            "non_field_errors": ["Unexpected argument(s): 'misplaced', 'nested', 'quoted', 'team'."]
        }

    def test_naming_both_markers_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="both InputRequired and NotClientInput"):
            marked_input_keys(_by_name_contradiction)


# ------------------------------------------------- annotations compiled without PEP 563

_EAGER_SOURCE = """
from typing import TYPE_CHECKING, Annotated

from rest_framework_services import InputRequired, NotClientInput

if TYPE_CHECKING:
    from tests.testapp.models import Post


def selector(
    *,
    team: Annotated["Post", NotClientInput] = None,
    limit: "Annotated[Post, InputRequired]" = None,
    owner: "Post" = None,
) -> None: ...
"""

_LAZY_SOURCE = """
from typing import TYPE_CHECKING, Annotated

from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services import InputRequired, NotClientInput

_Hide = NotClientInput

if TYPE_CHECKING:
    from tests.testapp.models import Post


class Extras(TypedDict):
    team: NotRequired[Annotated[Post, _Hide]]
    limit: Annotated[Post, InputRequired] | None


def keyed(**extras: Unpack[Extras]) -> None: ...
"""

_UNIMPORTED_MARKER_SOURCE = """
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from rest_framework_services import NotClientInput


def selector(*, team: Annotated[str, NotClientInput] = "own-team", limit: int = 10) -> str:
    return team
"""


@pytest.fixture
def compiled(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], types.ModuleType]]:
    """Compile a source as its own module, without this module's postponed annotations."""

    def _compile(source: str) -> types.ModuleType:
        module = types.ModuleType("unresolved_annotation_markers")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        exec(compile(source, module.__name__, "exec", dont_inherit=True), module.__dict__)
        return module

    yield _compile


def test_a_forward_reference_inside_an_evaluated_annotation(
    compiled: Callable[[str], types.ModuleType],
) -> None:
    # Evaluated at definition, so ``team``'s marker is an object around a forward
    # reference, and ``limit`` is the whole string.
    module = compiled(_EAGER_SOURCE)

    assert marked_input_keys(module.selector) == (frozenset({"limit"}), frozenset({"team"}))


@pytest.mark.skipif(sys.version_info < (3, 14), reason="eager annotations raise at definition")
def test_lazily_evaluated_annotations_are_read_one_at_a_time(
    compiled: Callable[[str], types.ModuleType],
) -> None:
    # Each key is one forward reference naming no module, so it is read where
    # its class was written, the one module that defines ``_Hide``.
    module = compiled(_LAZY_SOURCE)

    assert marked_input_keys(module.keyed) == (frozenset({"limit"}), frozenset({"team"}))


def test_a_marker_whose_own_name_does_not_resolve_is_read_by_name(
    compiled: Callable[[str], types.ModuleType],
) -> None:
    module = compiled(_UNIMPORTED_MARKER_SOURCE)
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=module.selector)

    assert dispatch_spec(spec, user=None, params={"team": "other-team"}).value == "own-team"
    assert spec_to_json_schema(spec)["properties"] == {"limit": {"type": "integer"}}


_BASE_SOURCE = """
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from typing_extensions import TypedDict

from rest_framework_services import NotClientInput

_Hide = NotClientInput

if TYPE_CHECKING:
    from tests.testapp.models import Post


class Base(TypedDict, total=False):
    team: Annotated[Post, _Hide]
"""

_SUBCLASS_SOURCE = """
from __future__ import annotations

from typing_extensions import TypedDict, Unpack

from unresolved_annotation_base import Base


class Sub(Base, total=False):
    limit: int


def selector(**extras: Unpack[Sub]) -> None: ...
"""


def test_an_inherited_key_is_read_where_its_class_was_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``_Hide`` exists only in the base's module, so ``team`` is hidden only when
    # its annotation is evaluated there rather than in the subclass's.
    for name, source in [
        ("unresolved_annotation_base", _BASE_SOURCE),
        ("unresolved_annotation_subclass", _SUBCLASS_SOURCE),
    ]:
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(source, name, "exec", dont_inherit=True), module.__dict__)

    assert marked_input_keys(module.selector) == (_NOTHING, frozenset({"team"}))


_PEP_695_SOURCE = """
from __future__ import annotations

from typing_extensions import TypedDict, Unpack


class Box[T](TypedDict, total=False):
    tag: T


def selector[T](*, pk: int = 0, **extras: Unpack[Box[T]]) -> T | None:
    return extras.get("tag")
"""


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax is new in 3.12")
def test_a_type_parameter_resolves_beside_the_callables_own(
    compiled: Callable[[str], types.ModuleType],
) -> None:
    # ``T`` lives in ``selector.__type_params__``, never in the module, so the
    # ``**extras`` annotation resolves, and its surface is known, only beside them.
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=compiled(_PEP_695_SOURCE).selector)

    result = dispatch_spec(
        spec, user=None, params={"tag": "x"}, unknown_arguments=UnknownArguments.REJECT
    )

    assert result.value == "x"


# ------------------------------------------------- as_view() reads them as dispatch does


def _misplaced_beside(
    *, team: list[Annotated[int, NotClientInput]] | None = None, owner: _Owner | None = None
) -> list[Any]:
    return []


def _contradiction_beside(
    *, team: Annotated[int, InputRequired, NotClientInput] = 0, owner: _Owner | None = None
) -> list[Any]:
    return []


def _misplaced_in_unresolved(
    *, team: list[Annotated[_Owner, NotClientInput]] | None = None
) -> list[Any]:
    return []


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        pytest.param(_misplaced_beside, "a schema marker", id="misplaced-beside"),
        pytest.param(_contradiction_beside, "both InputRequired and NotClientInput", id="both"),
        pytest.param(_misplaced_in_unresolved, "a schema marker", id="misplaced-in-unresolved"),
    ],
)
def test_as_view_refuses_what_dispatch_would(selector: Any, message: str) -> None:
    """Before, the unresolved ``owner`` made every marker on the callable unread,
    so ``as_view()`` mounted each of these and dispatch never refused them either."""

    class _View(SelectorListView):
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)

    with pytest.raises(ImproperlyConfigured, match=message):
        _View.as_view()


def test_as_view_still_mounts_a_well_placed_marker_beside_an_unresolved_name() -> None:
    class _View(ServiceCreateView):
        spec = ServiceSpec(service=_hidden)

    _View.as_view()
