"""Internal helpers shared across the ``types`` package.

``pk_input_targets`` is one exception to the package name: the write
path shares it, so the three spellings of a primary key are written down
once rather than agreed between two modules. ``callable_type_hints`` is the
other: the input-schema reflection shares it, so the schema and dispatch read
a callable's markers off the same annotations, and ``typed_dict_input`` reads a
``TypedDict``'s keys through the same ``annotation_hints``.
"""

from __future__ import annotations

import builtins
import inspect
import re
import sys
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Annotated, Any, Final, ForwardRef, get_args

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from typing_extensions import Format, get_annotations, get_type_hints

from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_orphan import RelationOrphan


def validate_metadata(metadata: Any, *, label: str) -> None:
    """Reject a non-mapping ``metadata`` declaration on a spec.

    Called from ``__post_init__``, not from ``as_view()`` like every other spec
    field: ``metadata`` is read by consumers that may never mount the spec on a
    view, so a view-time check would skip the paths the field exists for.
    Shape-only — the framework never reads the mapping's contents.
    """
    if metadata is None or isinstance(metadata, Mapping):
        return
    raise ImproperlyConfigured(
        f"{label}.metadata must be a mapping (or None); got "
        f"{type(metadata).__name__}. The framework never reads its contents, "
        f"but the shape is fixed so consumers can rely on it."
    )


VALID_RELATION_MODES = tuple(mode.value for mode in RelationMode)


def validate_relation_mode(mode: str, *, label: str) -> None:
    """Reject a ``mode`` that is neither ``"replace"`` nor ``"merge"``.

    Shared by every relation kind that reconciles a collection so the two words
    mean the same thing on all of them.
    """
    if mode not in VALID_RELATION_MODES:
        raise ValueError(f"{label}.mode must be one of {VALID_RELATION_MODES}; got {mode!r}.")


VALID_RELATION_ORPHANS = tuple(orphan.value for orphan in RelationOrphan)


def validate_relation_orphan(orphan: str, *, delete_service: Any, label: str) -> None:
    """Check ``orphan``, and refuse it beside the service that would silence it.

    A ``delete_service`` replaces the unlink-or-delete rule outright, so an
    explicit ``orphan`` beside one would decide nothing and be silently ignored.
    ``AUTO`` is exempt because it states nothing — it is what every spec written
    before the field existed carries.
    """
    if orphan not in VALID_RELATION_ORPHANS:
        raise ValueError(f"{label}.orphan must be one of {VALID_RELATION_ORPHANS}; got {orphan!r}.")
    if delete_service is None or orphan == RelationOrphan.AUTO:
        return
    raise ImproperlyConfigured(
        f"{label}: orphan={RelationOrphan(orphan).value!r} declared alongside delete_service. "
        "The service replaces the unlink-or-delete rule entirely, so the flag would decide "
        "nothing. Dispose of the row in the service, or drop the service and let orphan= say "
        "what happens to it."
    )


def validate_relation_services(
    *,
    label: str,
    services: Mapping[str, Any],
    shaping: Mapping[str, Any],
) -> None:
    """Refuse a relation spec that declares a row service *and* row shaping.

    A ``create_service`` / ``update_service`` stands in for the mutation-helper
    call, so every knob configuring that call is bypassed for the row — silently,
    unless caught here.

    Callers must keep reconciliation fields (``fk`` / ``match_key`` / ``mode`` /
    ``scope`` / orphan handling) out of both mappings, and must not pass
    ``delete_service`` in ``services``: it replaces the unlink-or-delete rule
    rather than the helper call, so it composes with row shaping.
    """
    declared_services: list[str] = sorted(name for name, value in services.items() if value)
    if not declared_services:
        return
    declared_shaping: list[str] = sorted(name for name, value in shaping.items() if value)
    if not declared_shaping:
        return
    raise ImproperlyConfigured(
        f"{label}: {', '.join(declared_services)} declared alongside "
        f"{', '.join(declared_shaping)}. A row service replaces the helper call "
        "that those configure, so they would be silently ignored. Drop them and "
        "let the service shape the row, or drop the service and let the helper "
        "write it. Reconciliation (fk / match_key / mode / scope / orphan "
        "handling) stays with the spec either way."
    )


def pk_input_targets(model: type[Model]) -> frozenset[str]:
    """The names an incoming row can use to mean ``model``'s primary key.

    Three spellings reach the same column and a payload may send any of them:
    ``pk``, the field's ``name``, and its ``attname`` (``id``/``id`` for an
    implicit key, ``author``/``author_id`` where a one-to-one *is* the key).
    """
    return frozenset({"pk", model._meta.pk.name, model._meta.pk.attname})


def validate_pk_field_map(
    *,
    label: str,
    model: type[Model],
    match_key: str,
    field_map: Mapping[str, str] | None,
) -> None:
    """Refuse a ``field_map`` that renames an input key onto the primary key,
    on a spec that also matches *by* the primary key.

    The two readers of a nested row disagree about ``field_map``, and where the
    match key is the primary key that disagreement leaves no working payload.
    Matching reads the row exactly as it arrived (``item[match_key]``), so a
    payload spelling its key ``ident`` never matches; the primary-key guard
    *does* fold ``field_map`` in, sees ``ident``, reads it as a key nothing
    matched, and refuses the row. Every payload using the mapped name is
    rejected, and none can ever match.

    Scoped to a primary-key ``match_key`` because that is the whole of what is
    unreachable. Mapped onto a *natural* match key the same declaration is
    coherent -- the row matches on its natural key and the alias goes on
    guarding creates, exactly as a plainly-spelled ``pk`` would.

    Refused rather than resolved: ``match_key`` names an input key on one side
    and a model field on the other, so "apply ``field_map`` to it too" has no
    single correct answer, and a spec no payload can reach is better caught
    where it is written than on the first request that tries.
    """
    targets: frozenset[str] = pk_input_targets(model)
    if match_key not in targets:
        return
    mapped: list[str] = sorted(src for src, dest in (field_map or {}).items() if dest in targets)
    if not mapped:
        return
    raise ImproperlyConfigured(
        f"{label}: field_map renames {', '.join(repr(name) for name in mapped)} onto the "
        f"primary key of {model.__name__}, which match_key={match_key!r} also matches on. "
        "Matching reads the row as it arrived and does not apply field_map, so a payload "
        "using the mapped name can never match, while the primary-key guard does apply it "
        "and refuses the row. Send the key under a name match_key reads, or match on a "
        "field the mapping does not rename."
    )


# The pool names a dispatch seeds per call rather than per process: the resolved
# target and the validated input. An affordance condition that reads one of them
# is a rule about *this call* -- what ``preconditions`` is for -- and could never
# be answered without attempting the call, which is the one thing an affordance
# exists to allow. The declaration refuses a callable naming them, and the
# dispatcher withholds them from one that takes ``**kwargs``, so the two cannot
# disagree about what a condition may see.
PER_CALL_POOL_NAMES: Final = frozenset({"instance", "collection", "data", "serializer"})


def is_row_condition(when: Any) -> bool:
    """True when an affordance's ``when`` is an ORM expression -- a condition on the row.

    ``resolve_expression`` is the protocol every ORM expression and ``Q``
    implements and no plain callable does, so it separates the two shapes
    without importing each expression class. Shared because the declaration,
    the enforcement and the list projection must all draw the line in the same
    place: a condition one of them treats as a callable and another as SQL is
    two definitions of one rule.
    """
    return hasattr(when, "resolve_expression")


def affordance_alias(name: str, code: str) -> str:
    """The annotation a list query carries one affordance's answer under.

    ``name`` is the key a ``SelectorSpec.affordances`` entry is declared under and
    ``code`` the affordance's own. Prefixed and double-underscored on purpose: a
    Django field name may not contain ``__``, so the alias can never shadow a
    model field, and the prefix keeps it clear of a model *method* of the same
    name, which ``annotate`` would silently overwrite on every instance.
    """
    return f"affordance__{name}__{code}"


def callable_type_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """The annotations of a service / selector's parameters, each read on its own, markers kept.

    The one place a callable's annotations are read for the schema markers
    (``InputRequired``, ``NotClientInput``, ``InputDescription``), by both the
    input-schema reflection and dispatch, so the two cannot disagree about which
    keys are required or hidden. ``typed_dict_input`` reads the keys of a
    ``TypedDict`` through the same ``annotation_hints``, so an ``Unpack``-ed key is
    read by the same rule as a parameter.

    **It never raises for an annotation that does not resolve.** Each name is
    resolved alone, so a parameter typed with a name imported only under ``if
    TYPE_CHECKING:`` costs that parameter and nothing else. Resolved all at once,
    as they were, one such name failed the whole read, and every reader took the
    callable as unmarked: its ``NotClientInput`` keys were advertised and took the
    caller's value, and its ``InputRequired`` keys were optional. The name that
    does not resolve is still read for its markers (``_read_unresolved``).

    The ``Annotated`` metadata the markers ride in is kept, which ``get_type_hints``
    strips by default. And each annotation is resolved apart from its callable, so
    no default is in sight: on Python 3.10, ``typing.get_type_hints`` wraps a
    parameter whose default is ``None`` in ``Optional[...]``, which put an
    ``Annotated[int | None, NotClientInput] = None`` marker below where it is read.
    ``test_not_client_input_survives_a_none_default`` and
    ``test_input_required_survives_a_none_default`` hold that on 3.10.
    """
    return annotation_hints(fn)


def annotation_hints(owner: Any) -> dict[str, Any]:
    """Every annotation ``owner`` declares, each resolved on its own, markers kept.

    ``owner`` is a callable, or a ``TypedDict`` whose keys are read the same way.
    Its annotations are taken in the ``FORWARDREF`` format, which hands back each
    one without evaluating the others: as written under postponed annotations, and
    on Python 3.14, where annotations are evaluated lazily, with a forward
    reference wherever a name does not resolve. Each is then resolved where it was
    written (``_namespaces``), and one that does not resolve is read by
    ``_read_unresolved``.

    A class is read base by base along its MRO, as ``get_type_hints`` reads one,
    so a dataclass taken as a callable keeps the markers its bases declared, each
    resolved in its own base's module
    (``test_a_class_reads_each_bases_annotations_where_it_was_written``). A
    ``TypedDict`` carries its inherited keys itself, and its MRO holds none of
    the classes it inherits them from.

    One whose annotations cannot be taken at all, such as one whose
    ``__annotations__`` is not a dict, has none here, as it had when
    ``get_type_hints`` raised for it
    (``test_annotations_that_cannot_be_taken_read_as_none``).
    """
    hints: dict[str, Any] = {}
    for declarer in reversed(getattr(owner, "__mro__", (owner,))):
        try:
            annotations = get_annotations(declarer, format=Format.FORWARDREF)
        except Exception:  # noqa: BLE001 — nothing to read, see the docstring
            continue
        namespace, local = _namespaces(declarer)
        hints.update((name, _read(value, namespace, local)) for name, value in annotations.items())
    return hints


def resolved_annotation(owner: Any, name: str) -> Any:
    """``owner``'s annotation for ``name``, resolved, or whatever resolving it raised.

    For a reader that must tell an annotation that resolves from one that does
    not, and reads one name to do it: a ``**kwargs: Unpack[SomeExtras]``
    annotation is a callable's whole keyword surface, so one that does not resolve
    leaves the surface unknown, while another parameter that does not resolve
    leaves it known.
    """
    namespace, local = _namespaces(owner)
    return _resolve(get_annotations(owner, format=Format.FORWARDREF)[name], namespace, local)


def _namespaces(owner: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(globals, locals)`` to resolve ``owner``'s annotations in.

    A callable's globals are those of the function it wraps, as ``get_type_hints``
    takes them; a class has none of its own, so it reads its module's. The locals
    are ``owner``'s PEP 695 type parameters, kept apart from the globals and built
    afresh for every read: up to 3.13 a forward reference answers with the value
    it last resolved to whenever it is evaluated again with one namespace standing
    for both.
    """
    namespace = getattr(inspect.unwrap(owner), "__globals__", None)
    if namespace is None:
        module = sys.modules.get(getattr(owner, "__module__", None) or "")
        namespace = getattr(module, "__dict__", {})
    local = {parameter.__name__: parameter for parameter in getattr(owner, "__type_params__", ())}
    return namespace, local


def _read(annotation: Any, namespace: dict[str, Any], local: dict[str, Any]) -> Any:
    try:
        return _resolve(annotation, namespace, local)
    except Exception:  # noqa: BLE001 — read as far as it goes instead
        return _read_unresolved(annotation, namespace, local)


def _resolve(annotation: Any, namespace: dict[str, Any], local: dict[str, Any]) -> Any:
    """``annotation`` resolved the way ``get_type_hints`` resolves one, ``Annotated`` kept.

    ``get_type_hints`` resolves every form an annotation arrives in here: a
    string, a forward reference, which resolves in its own module when it names
    one, as a ``TypedDict`` key inherited from another module's class does, and an
    evaluated annotation with a forward reference inside. It resolves all of an
    object's annotations at once, so it is handed a holder carrying only this one.
    """
    holder = SimpleNamespace(__annotations__={"value": annotation})
    return get_type_hints(holder, globalns=namespace, localns=local, include_extras=True)["value"]


def _read_unresolved(annotation: Any, namespace: dict[str, Any], local: dict[str, Any]) -> Any:
    """An annotation that does not resolve, read as far as it evaluates.

    Its markers are what matter here: a ``NotClientInput`` read as absent
    advertises the key and lets the caller's value reach the callable, and an
    ``InputRequired`` read as absent lets a call through without the key.

    **First, evaluated with stand-ins.** What does not resolve is nearly always a
    name imported under ``if TYPE_CHECKING:``, while the markers are imported at
    runtime. So the annotation's text is evaluated with every name nothing defines
    standing in for ``Any``: ``Annotated[Owner, NotClientInput] | None`` reads as
    ``Annotated[Any, NotClientInput] | None``. The markers then sit where
    ``read_schema_markers`` looks for them, so one placed too deep is still
    refused, and the rest of the type reaches the schema. The standard library's
    ``FORWARDREF`` evaluation does not get this far: up to 3.13 it keeps the whole
    text as one forward reference when any name in it does not resolve, and 3.14's
    does the same for that spelling
    (``test_forwardref_evaluation_keeps_the_whole_text_as_one_reference``). An
    annotation that is already an object with a forward reference inside (a quoted
    name, without postponed annotations) carries its markers as objects, and is
    kept as it is.

    **Then, by name.** What the stand-ins cannot reach is text: an attribute of a
    name that did not resolve (``models.Owner``), a forward reference the
    evaluation leaves in place, and the name of a marker that is itself what did
    not resolve. A marker there is honoured by its name, failing closed: unread
    text naming ``NotClientInput`` hides the key, and text naming ``InputRequired``
    requires it, with ``Any`` for its type. Text is the last resort because it
    cannot place a marker, and cannot read an ``InputDescription``'s text
    (``misplaced`` in ``test_a_named_marker_is_honoured``, ``described`` in
    ``test_the_schema_hides_and_requires_by_name``).

    Each source of text is held by a case of ``test_a_named_marker_is_honoured``:
    a failed evaluation by ``team``, a forward reference left inside by
    ``nested``, an evaluation that is itself text by ``quoted``; and a stand-in's
    name by ``test_a_marker_whose_own_name_does_not_resolve_is_read_by_name``.
    """
    text = (
        annotation if isinstance(annotation, str) else getattr(annotation, "__forward_arg__", None)
    )
    if text is None:
        value: Any = annotation
        unread: list[str] = []
    else:
        # A forward reference built from a ``TypedDict`` key names the module the
        # key was written in, which for an inherited key is not the class's own
        # (``test_an_inherited_key_is_read_where_its_class_was_written``).
        module = sys.modules.get(getattr(annotation, "__forward_module__", None) or "")
        scope = getattr(module, "__dict__", namespace)
        stand_ins = _StandIns(local, scope)
        try:
            # The same evaluation ``get_type_hints`` gives this text, with only
            # the names nothing defines replaced.
            value = eval(text, scope, stand_ins)
        except Exception:  # noqa: BLE001 — what the stand-ins cannot reach is read by name
            value, unread = Any, [text]
        else:
            unread = stand_ins.missing
    named = set(_MARKER_NAMES.findall(" ".join([*unread, *_forward_ref_texts(value)])))
    markers = tuple(marker for name, marker in _MARKERS if name in named)
    return Annotated[(Any, *markers)] if markers else value


# The markers read by name when only text is left of them. ``InputDescription`` is
# not among them: its text cannot be read off a name.
_MARKERS: Final = (("InputRequired", InputRequired), ("NotClientInput", NotClientInput))
_MARKER_NAMES: Final = re.compile(r"\b(InputRequired|NotClientInput)\b")


class _StandIns(dict[str, Any]):
    """The locals ``_read_unresolved`` evaluates in: a name nothing defines is ``Any``.

    ``eval`` looks a name up in its locals first, and a ``dict`` subclass answers a
    missing key through ``__missing__``, so this sees every name the text uses. It
    answers from the type parameters it holds, then the namespace, then the
    builtins, and only then with the stand-in, recording the name in ``missing``
    so a marker's name can still be read.
    """

    def __init__(self, local: dict[str, Any], namespace: dict[str, Any]) -> None:
        super().__init__(local)
        self.namespace = namespace
        self.missing: list[str] = []

    def __missing__(self, name: str) -> Any:
        if name in self.namespace:
            return self.namespace[name]
        if hasattr(builtins, name):
            return getattr(builtins, name)
        self.missing.append(name)
        return Any


def _forward_ref_texts(value: Any) -> list[str]:
    """The text of every forward reference left in ``value``, and of ``value`` if it is text."""
    if isinstance(value, (str, ForwardRef)):
        return [getattr(value, "__forward_arg__", value)]
    return [text for arg in get_args(value) for text in _forward_ref_texts(arg)]
