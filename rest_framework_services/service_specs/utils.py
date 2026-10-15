"""The serializer field walk the DRF adapter reads a serializer through.

A DRF serializer declares what it takes and what it renders in one set of
fields, so both directions are read here: what one field is, as a JSON type,
format, choices and labels, is the same whichever way it faces, and is read by
the direction-neutral helpers (``field_type``, ``field_choices``). Which fields
take part is not, so the input walk (``input_parameters``) selects its own: a
``HiddenField`` and a read-only field are no parameter, since DRF ignores what
a caller sends for either.

**The shape check runs in front of DRF**, so a type declared here is a type
the kernel refuses a call for before the serializer sees it. The rule
throughout is therefore that a declaration may be incomplete and never
stricter than the field: a field with no single JSON type of its own - a
``JSONField``, a related field, which DRF reads from a string as readily as
from a number, or a field this walk does not know - declares ``any``, which
the shape check passes on as it came. It is the same policy drfs' own input
schema follows, which writes ``{}`` for each of them.

What a field means is read as drfs' ``serializer_to_json_schema`` reads it, so
the two describe one serializer alike; the parity test names each place the
two dialects differ and why.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.http import QueryDict
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.pool.resolve_callable_kwargs import resolve_callable_kwargs
from django_service_specs.types.is_async import is_async
from django_service_specs.types.unset import UNSET as KERNEL_UNSET
from rest_framework import ISO_8601, relations, serializers
from rest_framework.settings import api_settings
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.service_specs.base_serializer_context import base_serializer_context
from rest_framework_services.service_specs.build_offline_context import build_offline_context
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks

MAX_SERIALIZER_APPEARANCES = 4
"""How many times one serializer class may appear on the walk's current path
before the next appearance is truncated to an open object.

drfs' own input schema's allowance, and for the same reason: a
self-referential serializer is an ordinary shape and must not raise
``RecursionError`` while a transport describes it, and a serializer bounding
its own depth with a countdown is usually written three levels below its root,
so four appearances describe it as written. Kept equal to drfs' so the two
truncate one declaration at the same node."""

_SCALARS: tuple[tuple[type[serializers.Field], str, str | None], ...] = (
    (serializers.BooleanField, "boolean", None),
    (serializers.IntegerField, "integer", None),
    (serializers.FloatField, "number", None),
    # A decimal is a string in the decimal format, which the shape check also
    # takes as a JSON number, as DRF's field does.
    (serializers.DecimalField, "string", "decimal"),
    (serializers.DateTimeField, "string", "date-time"),
    (serializers.DateField, "string", "date"),
    # Each parses a string, in a format the kernel has no name for.
    (serializers.TimeField, "string", None),
    (serializers.DurationField, "string", None),
    (serializers.UUIDField, "string", None),
    # Email, URL, Slug, Regex and IPAddress all subclass CharField.
    (serializers.CharField, "string", None),
    # An open mapping: no ``fields``, so the shape check takes any key. HStore
    # subclasses DictField.
    (serializers.DictField, "object", None),
)
"""The fields with one JSON type of their own, and its format. DRF's scalar
fields do not subclass one another across rows, so the order matters only
within the CharField family, which ends the string rows."""


def describe_serializer(declaration: Any) -> serializers.Serializer:
    """The serializer ``declaration`` describes its fields with: the call's own, inside one.

    ``declaration`` is what a drfs spec's ``input_serializer`` takes: a
    ``Serializer`` subclass, or a dataclass, which is read through a
    ``DataclassSerializer`` exactly as dispatch validates it. Anything else is
    refused with ``ImproperlyConfigured``.

    Inside an edge call it is built once for the call (``EdgeCallState.described``)
    and every description of the call reads that one. The call's own input
    serializer is built as DRF's view built the one serializer of a request:
    with the call's body as its ``data``, the row it acts on as its
    ``instance``, its ``partial`` flag (``describe_with``), and its context -
    the call's request and view, its view's input layers and the spec's
    ``input_serializer_context`` - so a ``get_fields`` or an ``__init__``
    reading ``initial_data``, ``instance``, ``partial`` or
    ``self.context["request"]`` describes the fields the call validates with
    (test_the_input_serializer_is_built_once_per_call,
    test_a_live_view_scopes_the_field_to_the_caller,
    test_an_offline_view_builds_the_fields_from_its_query_string_and_route,
    test_with_no_view_and_no_request_the_caller_is_the_principal). The
    Validator validates with that same serializer where it binds it exactly so
    (``bound_description``). Outside a call, as a schema or a registration
    describes it, it is built unbound, with the context DRF builds on HTTP with
    no view and no request: a ``get_fields`` reading
    ``self.context["request"]`` finds the key and reads ``None``, rather than
    raising ``KeyError`` from a schema while dispatch would have rendered it.
    What the serializer raises while it is described is raised as it is
    (test_a_serializer_that_fails_is_not_hidden_at_dispatch, which fails if a
    serializer is rebuilt where the call has no callable taking ``**kwargs``
    to ask).

    A spec's context callable taking ``**kwargs`` is left out of a description,
    which holds none of the call's seeds, unless the serializer cannot be built
    without it: then it is asked over what the description holds, and the
    serializer built once more with its answer
    (test_a_live_view_describes_a_serializer_needing_a_whole_pool_callable,
    test_off_http_a_serializer_needing_a_whole_pool_callable_is_described).
    What the serializer raises built with that answer is raised as it is
    (test_a_serializer_failing_with_a_whole_pool_callable_too_is_not_hidden).
    """
    state = EDGE_CALL.get()
    describe = state.describe_context if state is not None else None
    if state is None or describe is None:
        return bind_serializer(
            declaration, context=base_serializer_context(view=None, request=None)
        )
    described = state.described.get(declaration)
    if described is None:
        try:
            described = _described(declaration, state.describe_binding, describe)
        except (AttributeError, KeyError, TypeError):
            # Read before the target lookup has found the row the serializer
            # is built with, for the names the lookup's own arguments need, a
            # serializer that cannot be built without its row is taken to
            # declare no name, as a registration takes it
            # (test_a_serializer_reading_its_row_validates_off_http). Once the
            # row is found it is built with it, and what it raises then is
            # raised as it is
            # (test_a_serializer_failing_with_its_row_too_is_not_hidden).
            if not state.row_pending:
                raise
            described = serializers.Serializer()
        state.described[declaration] = described
    return _row_serializer(described)


def _row_serializer(serializer: serializers.Serializer) -> serializers.Serializer:
    """``serializer``, or the child a ``many`` call's list serializer validates each row with."""
    if isinstance(serializer, serializers.ListSerializer):
        return cast(serializers.Serializer, serializer.child)
    return serializer


def _described(
    declaration: Any,
    binding: DescribeBinding | None,
    describe: Callable[..., dict[str, Any] | None],
) -> serializers.Serializer:
    """``declaration`` built for the call: as the call binds it, where it is the call's own."""
    # Another declaration a description reads in the call - a kernel call's
    # inside the service - is no body's, and is described unbound
    # (test_another_declaration_is_described_for_no_body).
    bound: dict[str, Any] = {}
    asked: tuple[Any, ...] = ()
    if binding is not None and binding.declaration is declaration:
        bound, asked = binding.serializer_kwargs(), (binding.data,)
    try:
        described = bind_serializer(declaration, context=describe(*asked), **bound)
        # DRF builds the fields when they are first read; they are read here, so
        # a ``get_fields`` the description cannot answer fails where it can
        # still be asked again (the ``_FieldsFromTheSpec`` half of each test).
        _fields = _row_serializer(described).fields
        return described
    except (AttributeError, KeyError, TypeError):
        # How a read of a key the description lacks fails, each held by its case
        # of test_a_serializer_failing_any_way_without_a_whole_pool_callable_is_described.
        whole = describe(*asked, whole_pool=True)
        if whole is None:
            raise
        return bind_serializer(declaration, context=whole, **bound)


@dataclasses.dataclass(frozen=True, eq=False)
class DescribeBinding:
    """What an edge call binds its own input serializer to: its body, its row and its flags.

    ``data`` is the body the serializer validates, ``instance`` the row the
    call acts on (``None`` for a create, and for a collection, which is no
    row), ``partial`` the compiled spec's flag, and ``many`` whether the body
    is a list of rows.
    """

    declaration: Any
    data: Any
    instance: Any = None
    partial: bool = False
    many: bool = False

    def serializer_kwargs(self) -> dict[str, Any]:
        """The keywords the serializer is built with, as the Validator binds it."""
        if self.many:
            return {"data": self.data, "many": True, "partial": self.partial}
        return {"data": self.data, "instance": self.instance, "partial": self.partial}


def describe_with(state: EdgeCallState, binding: DescribeBinding) -> None:
    """Describe ``binding.declaration`` as ``binding`` binds it from here on in the call.

    The edge binds its input serializer to the caller's body as soon as the
    call starts, and again once it knows the body the serializer validates and
    the row it acts on, where either is another (``prepare_edge_call``): the
    description built for the first is dropped, and the next description of the
    call is built for the second.
    """
    state.describe_binding = binding
    state.described.pop(binding.declaration, None)


def bound_description(
    declaration: Any, context: Mapping[str, Any], **kwargs: Any
) -> serializers.Serializer | None:
    """The call's description of ``declaration``, where it is bound exactly as ``kwargs`` binds.

    The Validator binds the serializer that validates, and where the call's
    description was built with the same ``data`` object, the same row, the
    same ``partial`` and ``many`` and a context holding the same objects under
    the same keys, the description is that serializer, so the call builds its
    input serializer once, as DRF's view did
    (test_the_input_serializer_is_built_once_per_call). Anything else is built
    afresh: a body the server put a value into, a row a lookup resolved after
    the description, or a context callable the description could not ask
    (``TestAContextCallableTheDescriptionCannotAsk``). A description that has
    validated once is never validated again
    (test_a_kernel_call_inside_the_service_binds_its_own_arguments).
    """
    state = EDGE_CALL.get()
    described = state.described.get(declaration) if state is not None else None
    # One arc to coverage: test_never_described_it_is_none holds the first
    # condition and test_once_it_has_validated_it_is_none the second.
    if described is None or hasattr(described, "_errors"):
        return None
    if not _bound_as(described, kwargs) or not _same_objects(described._context, context):
        return None
    return described


def bound_instance(target: Any) -> Any:
    """The row a serializer binds for ``target``: none for a collection, which is no row to update."""
    return None if isinstance(target, QuerySet) else target


def _bound_as(serializer: serializers.Serializer, kwargs: Mapping[str, Any]) -> bool:
    """Whether ``serializer`` was built with ``kwargs``' body, row and flags.

    One arc to coverage, so each condition is named by its case of
    test_bound_another_way_it_is_none: ``many``, ``another-data``,
    ``another-row`` and ``partial``, in order.
    """
    return (
        isinstance(serializer, serializers.ListSerializer) is kwargs.get("many", False)
        and getattr(serializer, "initial_data", UNSET) is kwargs["data"]
        and serializer.instance is kwargs.get("instance")
        and serializer.partial == kwargs["partial"]
    )


def _same_objects(held: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """Whether ``held`` holds the very objects ``context`` does, under the same keys.

    Each half is named by its case of test_bound_another_way_it_is_none:
    ``another-key`` and ``another-object``.
    """
    return held.keys() == context.keys() and all(held[key] is context[key] for key in context)


def unexpected_arguments(names: Iterable[str]) -> serializers.ValidationError:
    """DRF's refusal of ``names`` as arguments the call has nowhere to put, in this package's words."""
    listed = ", ".join(repr(name) for name in sorted(names))
    return serializers.ValidationError(
        {
            api_settings.NON_FIELD_ERRORS_KEY: [
                serializers.ErrorDetail(f"Unexpected argument(s): {listed}.", code="invalid")
            ]
        }
    )


def writable_input_names(spec: Any) -> frozenset[str]:
    """The names a service spec's input serializer takes from the body at the top level.

    Each writable field's name, as the client sends it, read through
    ``describe_serializer``. A ``many`` spec's fields are each row's and a spec
    with no input serializer has none, so either answers no name; a read-only
    field and a ``HiddenField`` take nothing from the body (``is_input``), so
    neither is a name the body writes (the ``read-only-field`` and
    ``hidden-field`` cases of test_a_read_no_field_takes_from_the_body_is_accepted).
    """
    if spec.input_serializer is None or spec.many:
        return frozenset()
    fields = describe_serializer(spec.input_serializer).fields
    return frozenset(name for name, field in fields.items() if is_input(field))


def bind_serializer(declaration: Any, **kwargs: Any) -> serializers.Serializer:
    """``declaration`` built as a serializer with ``kwargs``, or ``ImproperlyConfigured``."""
    if isinstance(declaration, type) and issubclass(declaration, serializers.Serializer):
        return declaration(**kwargs)
    if isinstance(declaration, type) and dataclasses.is_dataclass(declaration):
        return DataclassSerializer(dataclass=declaration, **kwargs)
    raise ImproperlyConfigured(
        f"{declaration!r} cannot be read as a serializer: declare a Serializer subclass or a "
        "dataclass, as an input_serializer takes."
    )


SERIALIZER_CONTEXT_SEED = "_serializer_context_layers"

# The prefix of the name a server's value for a parameter is carried under
# where a filter of the same name takes the client's (``server_channel``).
SERVER_CHANNEL = "_server_owned_"


def server_channel(name: str) -> str:
    """The name the server's value for ``name`` travels under beside a filter's ``name``.

    No read declares it, so the kernel takes no client's value for it.
    """
    return SERVER_CHANNEL + name


def fills_its_own(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` is a wrapper taking ``**kwargs`` that may fill its own parameters.

    ``inspect.signature`` follows ``__wrapped__``, so a ``functools.wraps``
    decorator reports the wrapped function's parameters, while the wrapper is
    what runs and may fill any of them itself, as a decorator injecting a scope
    does. Which ones cannot be read, so none of them is one the call may refuse
    as missing (``left_to_the_callable``), as 0.57.0 asked none. Read off the
    author's own callable, never a compiled one: the compiler's own forwarding
    callables carry ``__wrapped__`` and a ``__signature__`` too. The two
    conjuncts, one branch arc, each held in
    ``tests/dispatch/test_a_wrapper_fills_its_own_parameter.py``:

    - the signature that runs differs from the one read, so ``fn`` is a wrapper:
      a plain ``**kwargs`` beside a required parameter fills nothing, and the
      parameter is still missing (test_a_parameter_beside_var_keyword_is_still_missing).
    - that signature takes ``**kwargs``: a wrapper naming its own parameters
      receives only what dispatch passes, so it cannot fill one either
      (test_a_wrapper_naming_its_own_parameters_is_still_refused).
    """
    read = inspect.signature(fn)
    try:
        runs = inspect.signature(fn, follow_wrapped=False)
    except (TypeError, ValueError):
        # A ``staticmethod`` or ``classmethod`` object: it has ``__wrapped__`` but
        # no signature of its own, and calling it calls what it wraps, so the
        # signature read is the one that runs
        # (test_a_staticmethod_is_read_through_not_skipped).
        runs = read
    return runs != read and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in runs.parameters.values()
    )


class _LeftToTheCallable:
    """The default ``left_to_the_callable`` reports: the callable fills the parameter or fails."""

    def __repr__(self) -> str:
        return "<left to the callable>"


LEFT_TO_THE_CALLABLE = _LeftToTheCallable()


def left_to_the_callable(fn: Callable[..., Any], names: frozenset[str] | None = None) -> Any:
    """``fn``, reporting a default for each of ``names`` it requires (every one, for ``None``).

    The kernel refuses a parameter the call left out as missing, by
    ``fn``'s signature, where a caller could have filled it. A parameter
    reported with a default is never refused: the call hands ``fn`` what the
    pool holds of it, and nothing where the pool holds nothing, so the callable
    fills it itself or fails as its own ``TypeError``, as on 0.57.0. Only the
    signature changes: ``fn`` is called as it was, and an ``async def`` stays
    one, since the kernel awaits it by its kind. ``fn`` itself is never
    stamped, as it may be the author's own function.
    """
    signature = inspect.signature(fn)
    # One arc to coverage, so each conjunct and the reordering are held by
    # test_only_a_named_required_parameter_is_left_to_the_callable: a default
    # stays the parameter's own, a positional-only parameter is never passed by
    # name, and a name not asked for keeps its requirement.
    relaxed = [
        parameter.replace(default=LEFT_TO_THE_CALLABLE)
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and (names is None or parameter.name in names)
        else parameter
        for parameter in signature.parameters.values()
    ]
    if is_async(fn):

        async def left(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)
    else:

        def left(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

    left.__name__ = getattr(fn, "__name__", "callable")
    left.__qualname__ = getattr(fn, "__qualname__", left.__name__)
    left.__module__ = getattr(fn, "__module__", None) or __name__
    # Read for an annotated ``**rest`` in ``fn``'s module, as the compiler's other
    # forwarding callables are; ``inspect.signature`` stops at ``__signature__``.
    left.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    cast(Any, left).__wrapped__ = fn
    cast(Any, left).__signature__ = signature.replace(parameters=_by_name(relaxed))
    return left


def _by_name(parameters: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """``parameters`` with every one dispatch passes by name reported keyword-only.

    Dispatch passes every argument by name, so the call is reported as it is
    made, and a parameter reported with a default before one reported with
    none is still a valid signature.
    """
    kinds = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return [
        *(p for p in parameters if p.kind is inspect.Parameter.POSITIONAL_ONLY),
        *(p for p in parameters if p.kind is inspect.Parameter.VAR_POSITIONAL),
        *(p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in parameters if p.kind in kinds),
        *(p for p in parameters if p.kind is inspect.Parameter.VAR_KEYWORD),
    ]


"""The key a DRF edge adds to a result's presentation context for the view's output layers.

Only ever a key of the ``ambient`` of a ``DispatchResult``'s
``PresentationContext``, added by the edge once the kernel has returned, never
a pool seed: the presenter runs when the transport renders, after the call, so
the view's ``output_serializer_context`` hook - lazy, handed the value
presented - travels with the result rather than through the call's pool, where
a callable taking a bare ``**kwargs`` would be handed it
(test_a_bare_kwargs_service_sees_no_edge_machinery)."""


@dataclasses.dataclass
class EdgeCallState:
    """What one call of the DRF edge carries for its own machinery, beside the kernel's pool.

    Everything here is the edge's, never an argument or a seed of the call, so
    it does not travel through the pool the kernel hands a callable taking a
    bare ``**kwargs``, which on main held ``progress``, ``request``,
    ``serializer``, ``user`` and the call's extras and nothing of the edge's
    (test_a_bare_kwargs_service_sees_no_edge_machinery). The edge sets it in
    ``EDGE_CALL`` around the whole call, and the compiled pieces that need it
    read it from there:

    - ``target_guard``: a selector spec's guard, run by the precondition the
      edge prepends (``guarded_selector``).
    - ``author_values``: a view hook's and the spec's ``kwargs`` provider's
      values naming no argument, which the compiled spec's ``kwargs`` seat
      (``author_values``) hands back over the Validator's values.
    - ``view_hooks``: the view's serializer context layers, whose input half
      the Validator reads (``input_view_layers``).
    - ``validator_input``: the Validator the edge dispatches, and the body it
      binds rather than the kernel's checked arguments (``validator_input``):
      the caller's own, a ``QueryDict`` kept one, with what the edge put over
      it, as DRF's serializer has always been handed the request's data.
    - ``server_values``: the server's value for each field of that
      Validator's body the edge put one into - a view hook's or the spec's
      ``kwargs`` provider's value naming the field, and ``input_data``, a field
      named like a seed of the call included - by the name the body carries,
      as the edge put it. The Validator validates these, never what a field
      reads back from the body, and a key a callable of the call marks
      ``NotClientInput`` is handed on by name only from one of them
      (``server_values``).
    - ``author_keys``: for each of those a view hook or the spec's ``kwargs``
      provider gave, the key the author gave it under, the field's wire name or
      its ``source``: the parameter the author named, which the Validator also
      hands the value to (``author_keys``).
    - ``provider_owned``: the keys the spec's typed ``kwargs`` provider is
      annotated to fill, which no client's value becomes, declined or not:
      the Validator hands the client's value for one on in ``data`` alone
      (``provider_owned``).
    - ``body_rows``: whether that body is a live view's list body, which the
      Validator binds whole, every row as sent, as DRF's view bound it
      (``SerializerValidator.validated_many``).
    - ``beside_rows``: the keys a ``many`` call sent beside its rows under
      ``many_argument`` that it declares no argument for, which the Validator
      refuses once the rows are valid, whatever the ``UnknownArguments``
      policy (``SerializerValidator.validated_many``).
    - ``row_errors``: what the list serializer bound to that whole body
      refused, as the installed DRF reports it, which the edge answers with
      in place of the kernel's rows (``edge_errors``).
    - ``describe_context``: builds the serializer context the call's input
      serializer is described with, its request, its view, the view's input
      layers and the spec's own context callable where the description can ask
      it, as the Validator binds it (``describe_serializer``). Asked with
      ``whole_pool=True``, it asks a callable taking ``**kwargs`` over what the
      description holds, or answers ``None`` where there is none. Built when a
      description asks, so a call describing nothing builds none.
    - ``base_context``: the request and view a serializer of the call was last
      built for, and the baseline context the view answered for them, so the
      view is asked once for every serializer of the call
      (``call_base_context``).
    - ``context_answers``: each answer a spec's context callable gave in the
      call, by the callable and the objects it was handed, so one handed the
      same objects is asked once for every serializer of the call
      (``context_answer``).
    - ``offline_pairs``: each offline request and view built in the call for
      a principal and the arguments they carry, so the descriptions of one
      call with neither share one pair (``offline_pair``).
    - ``describe_binding``: the body, row and flags the call binds its own
      input serializer to (``describe_with``).
    - ``described``: the serializer each declaration is described with in
      the call, built once (``describe_serializer``).
    - ``row_pending``: whether the row the call's input serializer is built
      with is one the target lookup has still to find, so a description that
      cannot be built without it declares no name until it is found
      (``describe_serializer``).
    - ``beside_filter``: the drfs selector spec - the call's own, or the
      service's target lookup - whose ``filter_set`` reads names its callables
      also take, and the value each callable is handed under each such name:
      what the caller sent as an argument, a route capture, an author's value,
      never ``filter_data`` and never what the filter reads
      (``values_beside_filter``).

    Mutable, so the edge can set it before the sync step it hops to a thread
    for and fill it there, and the Validator can fill it during the kernel's
    call: the thread runs in a copy of the context that holds this same object.
    """

    target_guard: Callable[[Any], None] | None = None
    author_values: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    view_hooks: ViewHooks = dataclasses.field(default_factory=ViewHooks)
    validator_input: tuple[Any, Any] | None = None
    server_values: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    author_keys: Mapping[str, str] = dataclasses.field(default_factory=dict)
    provider_owned: frozenset[str] = frozenset()
    body_rows: bool = False
    beside_rows: tuple[str, ...] = ()
    row_errors: Any = None
    describe_context: Callable[..., dict[str, Any] | None] | None = None
    base_context: tuple[Any, Any, dict[str, Any]] | None = None
    context_answers: dict[tuple[Any, ...], tuple[Any, Mapping[str, Any]]] = dataclasses.field(
        default_factory=dict
    )
    offline_pairs: dict[tuple[int, int], tuple[Any, Any, Any]] = dataclasses.field(
        default_factory=dict
    )
    describe_binding: DescribeBinding | None = None
    described: dict[Any, serializers.Serializer] = dataclasses.field(default_factory=dict)
    row_pending: bool = False
    beside_filter: tuple[Any, dict[str, Any]] | None = None


EDGE_CALL: ContextVar[EdgeCallState | None] = ContextVar(
    "rest_framework_services_edge_call", default=None
)
"""The DRF edge's state for the call in progress, or ``None`` outside one.

A context variable rather than a pool seed, so nothing the edge carries for
itself reaches a spec's callables. The kernel's sync steps run in this
context, and ``adispatch``'s thread hops run in a copy of it, which
``asgiref`` makes for every ``sync_to_async`` call."""


def edge_call_state() -> EdgeCallState:
    """The state of the edge call in progress, or an empty one outside any call."""
    return EDGE_CALL.get() or EdgeCallState()


def call_base_context(*, view: Any, request: Any) -> dict[str, Any]:
    """``base_serializer_context`` for ``view`` and ``request``, asked of the view once per call.

    The call's input serializer is described, and bound by its Validator, each
    with this baseline, and the view's ``get_serializer_context`` is asked for
    it once in the call, as DRF's view asks it once for the one serializer it
    builds (test_each_layer_is_asked_once_per_call). Each serializer is handed
    its own copy, so one writing to its context leaves the next as the view
    answered it (test_each_serializer_of_the_call_has_its_own_context).

    Held for the request and view it was asked for, and reused only for both:
    a kernel call made inside the service with another request, or another
    view, is answered for its own (test_a_kernel_call_inside_the_service_has_its_own_context,
    whose ``another-request`` case fails without the request check and whose
    ``another-view`` case fails without the view check).
    """
    state = EDGE_CALL.get()
    held = state.base_context if state is not None else None
    if held is not None and held[0] is request and held[1] is view:
        return dict(held[2])
    context = base_serializer_context(view=view, request=request)
    if state is not None:
        state.base_context = (request, view, context)
    return dict(context)


def validator_input(validator: Any, arguments: Any) -> Any:
    """What ``validator`` binds for the call: the edge's body for it, else ``arguments``.

    Only the Validator the edge call names reads the edge's body, so a kernel
    call made inside the service, of another spec or of this one, binds its
    own arguments (test_a_kernel_call_inside_the_service_binds_its_own_arguments).
    """
    held = edge_call_state().validator_input
    return held[1] if held is not None and held[0] is validator else arguments


def server_values(validator: Any) -> Mapping[str, Any]:
    """The server's value for each field of the body ``validator`` binds the edge put one into.

    Read as ``validator_input`` reads the body: only the Validator the edge call
    names, so a kernel call made inside the service, whose client may send the
    same field, takes none of the outer call's
    (test_a_kernel_call_inside_the_service_takes_none_of_the_outer_calls_server_values,
    which fails without the identity check). Outside an edge call nothing is
    held (test_a_kernel_dispatch_of_the_spec_keeps_the_client_value_in_data,
    which fails without the ``None`` check).
    """
    state = edge_call_state()
    return state.server_values if _names(state, validator) else {}


def write_form_value(body: QueryDict, name: str, value: Any) -> None:
    """Write ``value`` under ``name`` in a form body, as ``input_data`` always reached one.

    A list or tuple as the key's values (``setlist``), anything else as its one
    value (test_input_data_reaches_a_form_body_as_a_list). The edge writes the
    server's values into the body this way, and the Validator reads one back
    through its field the same way (``SerializerValidator``).
    """
    if isinstance(value, list | tuple):
        body.setlist(name, list(value))
    else:
        body[name] = value


def author_keys(validator: Any) -> Mapping[str, str]:
    """The key an author gave each of ``server_values`` under, read as ``server_values`` is."""
    state = edge_call_state()
    return state.author_keys if _names(state, validator) else {}


def provider_owned(validator: Any) -> frozenset[str]:
    """The keys the spec's typed ``kwargs`` provider fills, read as ``server_values`` is.

    Only for the Validator the edge call names, so a spec the service dispatches
    through the kernel hands its own parameter of an owned name its value
    (test_a_kernel_call_inside_the_service_keeps_its_own_values, which fails
    without the identity check).
    """
    state = edge_call_state()
    return state.provider_owned if _names(state, validator) else frozenset()


def _names(state: EdgeCallState, validator: Any) -> bool:
    held = state.validator_input
    return held is not None and held[0] is validator


def values_beside_filter(spec: Any) -> dict[str, Any] | None:
    """What the edge call in progress hands ``spec``'s callables under its filter's names.

    ``None`` outside an edge call that prepared them for ``spec`` - a
    dispatch of the compiled spec through the kernel's own ``dispatch``, or
    another spec's call - where a callable is handed what the kernel's pool
    holds (test_through_the_kernel_a_selector_still_reads_the_filter_s_value,
    whose ``held`` check it is; the spec check is held by
    test_a_kernel_call_inside_the_selector_is_handed_its_own_pool).
    """
    held = edge_call_state().beside_filter
    return held[1] if held is not None and held[0] is spec else None


def author_values() -> dict[str, Any]:
    """The author values a DRF edge carries for the call, as a ``kwargs`` provider answers.

    The compiled form of a service spec the edge dispatches carries this as its
    ``kwargs`` provider (``kernel_spec_variant(server_input=False)``), since the
    edge calls the spec's own provider itself. Empty outside an edge call, and
    for a call that carries none.
    """
    return dict(edge_call_state().author_values)


def input_view_layers() -> Mapping[str, Any] | None:
    """The input half of the view hooks the edge call carries, or ``None``."""
    return edge_call_state().view_hooks.input_serializer_context


def output_view_layers(ambient: Mapping[str, Any], value: Any) -> Mapping[str, Any] | None:
    """The output half of the view hooks the call registered, resolved for ``value``, or ``None``.

    Lazy on the carrier, because a view's output hook may declare the
    ``result`` it renders, which exists only once the call has run. One branch
    to coverage, so each condition is named by the test that fails without it:
    test_the_output_callable_reaches_the_output_serializer (no hooks
    registered, a call that reached no edge) and
    test_an_input_half_alone_reaches_the_input_serializer (hooks registered
    with no output half).
    """
    hooks: ViewHooks | None = ambient.get(SERIALIZER_CONTEXT_SEED)
    if hooks is None or hooks.output_serializer_context is None:
        return None
    return hooks.output_serializer_context(value)


def serializer_context(
    principal: Any,
    ambient: Mapping[str, Any],
    arguments: Mapping[str, Any] | list[Any] | None,
    *,
    view_layers: Mapping[str, Any] | None = None,
    provider: Callable[..., Mapping[str, Any]] | None = None,
    extras: Mapping[str, Any] | None = None,
    describing: bool = False,
) -> dict[str, Any]:
    """The serializer context a call's serializer is built under: four layers, later wins.

    1. **DRF's own.** The ``request`` and ``view`` the call registered are the
       serializer's own, as they are on HTTP, so a field or a ``validate()``
       reading either finds what the transport put there; a registered view
       that answers ``get_serializer_context`` supplies the whole context, as
       DRF's views do. **Only when the call registered neither** is an offline
       pair built, with ``principal`` as the request's ``user`` and
       ``arguments`` as its data, so a ``CurrentUserDefault`` reads the
       principal and a serializer reading ``self.context["request"]`` finds a
       request off HTTP too. One seed alone is kept as it is, beside a ``None``
       for the other: pairing a registered request with a view built around
       another one would describe a call that never happened.
    2. and 3. **The view's hooks**, ``view_layers``: the directional and
       per-action hooks the view resolved, one direction's half of what the
       edge carries: the input half from the call's ``EdgeCallState``, the
       output half from the result's presentation context, under
       ``SERIALIZER_CONTEXT_SEED``.
    4. **The spec's own callable**, ``provider``, which has the final say. It
       is called through the kernel's declared-kwargs resolver, as every other
       spec callable is, from a pool of the call's ``arguments`` (a mapping's
       entries; a ``many`` call's rows are no names, held by
       test_a_many_spec_s_rows_validate_under_the_input_callable_once), beneath its ``ambient``
       seeds, beneath the ``request`` and ``view`` of layer 1 and ``user`` (the
       principal), beneath ``extras`` (the value an output callable renders).
       A client's argument never stands in for one of the call's own objects
       (test_an_argument_never_stands_in_for_the_request_the_callable_declares).
       A key it answers with ``UNSET`` is declined, leaving the layer beneath,
       as on every transport drfs has (test_an_unset_value_declines_the_key_and_keeps_the_layer_beneath).

    One branch to coverage for the offline pair: the request seed alone is held
    by test_a_registered_request_alone_is_kept_beside_no_view, the view seed
    alone by test_a_registered_view_alone_answers_for_the_serializer_context,
    and neither by test_the_serializer_context_carries_an_offline_request_for_the_principal.
    """
    request = ambient.get("request")
    view = ambient.get("view")
    if request is None and view is None:
        offline = offline_pair(principal, arguments)
        request, view = offline.request, offline.view
    context = call_base_context(view=view, request=request)
    context.update(view_layers or {})
    if provider is not None:
        pool: dict[str, Any] = {
            **(arguments if isinstance(arguments, Mapping) else {}),
            # The edge's key for the output layers is no seed of the call, and
            # stays out of a callable's pool as every seed-free value does
            # (test_a_bare_kwargs_service_sees_no_edge_machinery).
            **{name: value for name, value in ambient.items() if name != SERIALIZER_CONTEXT_SEED},
            "request": request,
            "view": view,
            "user": principal,
            **(extras or {}),
        }
        # A description is made before the call's seeds are resolved and its
        # arguments routed, so a callable needing one of them is asked only by
        # the serializer that validates
        # (test_a_context_callable_needing_a_seed_is_asked_once_the_seed_exists).
        if not describing or _answerable(provider, pool):
            resolved = context_answer(provider, resolve_callable_kwargs(provider, pool))
            context.update({key: value for key, value in resolved.items() if value is not UNSET})
    return context


def offline_pair(principal: Any, arguments: Any) -> Any:
    """``build_offline_context(principal, arguments)``, built once in an edge call for both.

    A call handed no request and no view is described several times, each
    with the offline pair its serializer is built with, so its context
    callable, handed the same pair, is asked once for them all
    (test_with_no_view_and_no_request_the_context_callable_is_asked_once_per_body).
    Reused only for the same principal and the same arguments, which the
    request carries as its data: the serializer that validates a body the edge
    put a server's value into binds another, and its request carries that one
    (test_an_offline_pair_is_built_for_its_own_principal_and_arguments, whose
    cases fail without either half of the key).
    """
    state = EDGE_CALL.get()
    if state is None:
        return build_offline_context(principal, arguments)
    key = (id(principal), id(arguments))
    held = state.offline_pairs.get(key)
    if held is None:
        held = (principal, arguments, build_offline_context(principal, arguments))
        state.offline_pairs[key] = held
    return held[2]


def _answerable(fn: Callable[..., Any], pool: Mapping[str, Any]) -> bool:
    """Whether a description can ask ``fn``: it names what it needs, and ``pool`` holds it.

    A callable taking ``**kwargs`` is handed the whole pool, and a
    description's pool lacks the call's seeds, so it is asked by the
    serializer that validates alone, once, as it was handed the whole pool
    once (test_a_context_callable_taking_the_whole_pool_is_asked_once, which
    fails without the check), unless the serializer cannot be described
    without it (``describe_serializer``). A parameter named with no default must be in
    ``pool``: without the default check the ``a-default`` case of
    test_a_context_callable_the_description_can_answer_describes fails, and
    without the kind check, which a ``*args`` with no default fails, its
    ``positional-rest`` case.
    """
    if takes_the_whole_pool(fn):
        return False
    parameters = inspect.signature(fn).parameters.values()
    return all(
        parameter.name in pool
        for parameter in parameters
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )


def takes_the_whole_pool(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` takes ``**kwargs``, and so is handed every name of its pool."""
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(fn).parameters.values()
    )


def context_answer(
    provider: Callable[..., Mapping[str, Any]], kwargs: Mapping[str, Any]
) -> Mapping[str, Any]:
    """``provider(**kwargs)``, asked once in an edge call for the same callable and objects.

    The call's input serializer is described before it validates, each with
    the spec's context callable's layer, and the callable is asked once for
    both, as DRF's view asks its context once for the one serializer it builds
    (test_off_http_the_spec_s_context_describes_too). An answer is reused only
    for the same callable handed the same objects under the same names: a
    callable reading an argument the edge put a server's value over is asked
    again for it (test_a_context_callable_reading_an_argument_validates_with_the_server_s_value),
    and another spec's callable handed the same objects is asked itself
    (test_a_kernel_call_inside_the_service_asks_its_own_context_callable), and
    so is one handed the same object under another name
    (test_a_context_answer_is_held_for_the_names_its_objects_came_under). The
    objects are held with the answer, so none is freed and its identity reused while
    the call lasts.
    """
    state = EDGE_CALL.get()
    if state is None:
        return provider(**kwargs)
    key = (id(provider), *((name, id(kwargs[name])) for name in sorted(kwargs)))
    held = state.context_answers.get(key)
    if held is None:
        held = ((provider, dict(kwargs)), provider(**kwargs))
        state.context_answers[key] = held
    return held[1]


def is_input(field: serializers.Field) -> bool:
    """Whether a caller's value for ``field`` reaches the serializer at all.

    A read-only field is rendered and never read, and a ``HiddenField`` reads
    only its default, whatever a caller sends; so neither is a parameter, and
    under the kernel's closed argument set a caller sending either is refused
    rather than silently ignored. One branch to coverage, so each condition
    is held by its own field of
    test_a_hidden_field_and_every_read_only_field_are_not_parameters: ``who``
    (the ``HiddenField``) and the read-only rest.
    """
    return not (field.read_only or isinstance(field, serializers.HiddenField))


def input_parameters(
    serializer: serializers.Serializer, path: tuple[type[Any], ...] = ()
) -> Parameters:
    """What ``serializer`` takes, as Parameters, in field order. Never queries.

    ``path`` carries the serializer classes being described, innermost last,
    which is how a recursive declaration is bounded: see
    ``MAX_SERIALIZER_APPEARANCES``. A caller leaves it empty.
    """
    path = (*path, type(serializer))
    return Parameters(
        tuple(
            input_parameter(name, field, path)
            for name, field in serializer.fields.items()
            if is_input(field)
        )
    )


def input_parameter(name: str, field: serializers.Field, path: tuple[type[Any], ...]) -> Parameter:
    """One input field as a Parameter, named for the wire.

    ``required`` is the field's own, which DRF already reads from a default
    and from ``read_only``; ``nullable`` is its ``allow_null``; ``help`` its
    ``help_text``, rendered as a string in the language active now. A default
    is reported only when it is a literal (see ``literal_default``).
    """
    common: dict[str, Any] = {
        "required": field.required,
        "nullable": field.allow_null,
        "default": literal_default(field),
        "help": str(field.help_text) if field.help_text else None,
    }
    if isinstance(field, serializers.ListSerializer):
        # ``many=True`` on a nested serializer: an array of rows. The wrapper is
        # no serializer level of its own, so it costs no appearance.
        return Parameter(
            name,
            "array",
            items=_rows(field.child, path),
            **_lengths(field),
            **common,
        )
    if isinstance(field, serializers.Serializer):
        if _truncated(field, path):
            return Parameter(name, "object", **common)
        return Parameter(name, "object", fields=input_parameters(field, path), **common)
    # One branch to coverage, each class held by its own tests:
    # test_a_list_field_declares_its_element_and_its_length and
    # test_a_multiple_choice_field_is_an_array_whose_elements_are_the_choices.
    if isinstance(field, serializers.ListField | serializers.MultipleChoiceField):
        return Parameter(name, "array", **_element(field, path), **_lengths(field), **common)
    if isinstance(field, relations.ManyRelatedField):
        # A list of keys, each read as its related field reads one: from a
        # string as readily as from a number, so the element declares no type.
        return Parameter(name, "array", **common)
    json_type, fmt = input_type(field)
    choices = field_choices(field)
    if choices is None:
        return Parameter(name, json_type, format=fmt, **common)
    if choices.type is None:
        # No one JSON type for the values, and ``any`` lists no choices.
        return Parameter(name, "any", **common)
    return Parameter(
        name,
        choices.type,
        choices=choices.values,
        choice_labels=choices.labels,
        **common,
    )


def field_type(field: serializers.Field) -> tuple[str, str | None]:
    """The JSON type and format of one value ``field`` takes or renders.

    Direction-neutral. A file is a ``file``; a field with no single JSON type
    of its own - a ``JSONField``, a related field, a ``ModelField``, a custom
    field - is ``any``, so nothing is claimed that the field does not hold to.
    A choice field's type is its values', read by ``field_choices``.
    """
    if isinstance(field, serializers.FileField):
        # ImageField included.
        return "file", None
    for cls, json_type, fmt in _SCALARS:
        if isinstance(field, cls):
            return json_type, fmt
    return "any", None


def input_type(field: serializers.Field) -> tuple[str, str | None]:
    """The JSON type and format of one value ``field`` takes, for the shape check in front of it.

    ``field_type``, refined by what only the input side reads, so that the
    declaration is never stricter than the field:

    - a date or date-time read in any format but ISO 8601, its own or the
      project's ``DATE_INPUT_FORMATS`` / ``DATETIME_INPUT_FORMATS``, declares
      no format, since the kernel's names for the two are ISO's and would
      refuse ``14/10/2026`` before a field reading ``%d/%m/%Y`` saw it;
    - a localized decimal declares no type, since it reads the separators of
      the language active for the call, ``1,5`` in German, as well as a JSON
      number.
    """
    json_type, fmt = field_type(field)
    formats = _input_formats(field)
    # One branch to coverage: without the ``None`` test every other field
    # raises TypeError, and without the formats test
    # test_each_field_declares_its_json_type_and_format[date] declares no format for an ISO date.
    if formats is not None and any(each != ISO_8601 for each in formats):
        return json_type, None
    # One branch to coverage: without the isinstance every field but a
    # decimal raises AttributeError, and test_each_field_declares_its_json_type_and_format[decimal]
    # holds ``localize``.
    if isinstance(field, serializers.DecimalField) and field.localize:
        return "any", None
    return json_type, fmt


def _input_formats(field: serializers.Field) -> Sequence[str] | None:
    """The formats a date or date-time field reads, as DRF reads them when it validates.

    The field's own ``input_formats``, else the project's setting, read now
    rather than when the field was built, as DRF reads it. ``None`` for any
    other field.
    """
    # DateTimeField does not subclass DateField, so the order is free.
    if isinstance(field, serializers.DateTimeField):
        return getattr(field, "input_formats", api_settings.DATETIME_INPUT_FORMATS)
    if isinstance(field, serializers.DateField):
        return getattr(field, "input_formats", api_settings.DATE_INPUT_FORMATS)
    return None


@dataclasses.dataclass(frozen=True)
class FieldChoices:
    """A choice field's values, their shared JSON type, and their labels.

    ``type`` is ``None`` when the values share no single JSON type, which a
    declaration cannot list choices for. ``labels`` are the display DRF holds
    for each value, unrendered, so a lazy translation is rendered when a schema
    is built.
    """

    type: str | None
    values: tuple[Any, ...]
    labels: tuple[tuple[Any, Any], ...]


def field_choices(field: serializers.Field) -> FieldChoices | None:
    """The choices ``field`` takes, or ``None`` for a field that is no choice field.

    DRF's ``choices`` are already flat, option groups included, keyed by the
    value as declared. A ``ChoiceField`` that allows blank also takes ``""``,
    which its choices need not list; it is added, since a declaration of
    choices claims every value accepted, and the shape check would otherwise
    refuse the blank DRF takes. ``None`` under ``allow_null`` is the
    parameter's ``nullable``, not a choice.

    DRF matches a value by its ``str``, so it also takes ``"1"`` for the choice
    ``1``; the declaration states the type the values are written in, as drfs'
    own schema does.
    """
    if not isinstance(field, serializers.ChoiceField):
        return None
    return _choices_of(field)


def _choices_of(field: serializers.ChoiceField) -> FieldChoices:
    """``field_choices`` for a field known to be a choice field."""
    labels = tuple(field.choices.items())
    values = tuple(value for value, _label in labels)
    # One branch to coverage: test_allow_blank_adds_the_empty_string_to_string_choices
    # holds the flag, and test_allow_blank_does_not_list_the_empty_string_twice
    # the membership, without which a blank choice is listed twice.
    if getattr(field, "allow_blank", False) and "" not in values:
        values = (*values, "")
    found = {_json_type(value) for value in values} or {"string"}
    # ``None`` in the set is a value of no JSON scalar type, such as a
    # ``None`` choice itself; with it, as with two types, no single type holds.
    shared = found.pop() if len(found) == 1 else None
    return FieldChoices(type=shared, values=values, labels=labels)


def literal_default(field: serializers.Field) -> Any:
    """``field``'s default as a declaration a transport could print, or the kernel's ``UNSET``.

    A callable default - a function, ``CurrentUserDefault``,
    ``CreateOnlyDefault`` - is computed per call, so no one value is its
    default. DRF's ``empty`` and drfs' ``UNSET`` both mean no default; ``empty``
    is a class, so it is callable, and the one test answers for it too
    (test_a_required_field_is_required_and_takes_no_null). One branch to
    coverage, so each condition is held by its own test:
    test_the_unset_sentinel_as_a_default_is_no_default (``UNSET``) and
    test_a_callable_default_is_no_literal_default (the callable, ``empty``
    among them).
    """
    default: Any = field.default
    if default is UNSET or callable(default):
        return KERNEL_UNSET
    return default


def _json_type(value: Any) -> str | None:
    """The JSON type of a scalar value, or ``None`` for anything else.

    ``bool`` first: it subclasses ``int``, and ``true`` is not an integer on
    any wire.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _truncated(serializer: serializers.Serializer, path: tuple[type[Any], ...]) -> bool:
    return path.count(type(serializer)) >= MAX_SERIALIZER_APPEARANCES


def _rows(child: Any, path: tuple[type[Any], ...]) -> Parameters | str:
    """A list's rows: the child serializer's Parameters, or an open object past the bound."""
    return "object" if _truncated(child, path) else input_parameters(child, path)


def _element(
    field: serializers.ListField | serializers.MultipleChoiceField, path: tuple[type[Any], ...]
) -> dict[str, Any]:
    """What a list's element declares, as the array's own keywords.

    An array's ``items`` holds the element's JSON type, or its Parameters for
    a row; its format and choices describe each element and so sit on the
    array. Only a decimal's format is one an element takes: a list of dates
    is a list of strings, which the field decodes.
    """
    if isinstance(field, serializers.MultipleChoiceField):
        own = _choices_of(field)
        if own.type is None:
            return {}
        return {"items": own.type, "choices": own.values, "choice_labels": own.labels}
    child: Any = field.child
    if isinstance(child, serializers.Serializer):
        return {"items": _rows(child, path)}
    # One branch to coverage, each class held by its own case of
    # test_a_list_field_s_element_type: [list] and [list-of-rows].
    if isinstance(child, serializers.ListField | serializers.ListSerializer):
        return {"items": "array"}
    choices = field_choices(child)
    # One branch to coverage: test_a_list_of_choices_declares_them_on_the_array
    # holds the first condition, and test_a_list_of_mixed_choices_declares_none
    # the second, without which a list of mixed choices lists them on an
    # untyped array, and the shape check refuses the ``"1"`` DRF matches to ``1``.
    if choices is not None and choices.type is not None:
        return {
            "items": choices.type,
            "items_nullable": child.allow_null,
            "choices": choices.values,
            "choice_labels": choices.labels,
        }
    json_type, fmt = input_type(child)
    if json_type == "any":
        # An element of no single type, a choice field's of mixed values among
        # them: an array that declares no ``items`` takes any element already.
        return {}
    return {
        "items": json_type,
        "items_nullable": child.allow_null,
        "format": fmt if fmt == "decimal" else None,
    }


def _lengths(field: Any) -> dict[str, Any]:
    """A list's ``min_length`` and ``max_length``, with ``allow_empty=False`` a minimum of one.

    Declared so the shape check holds a list to what DRF holds it to; a list
    declaring no maximum is held to the kernel's own default by the shape
    check, which drfs' schema never stated.
    """
    minimum: int | None = getattr(field, "min_length", None)
    if not field.allow_empty:
        minimum = max(minimum or 0, 1)
    return {"min_length": minimum or None, "max_length": getattr(field, "max_length", None)}
