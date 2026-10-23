"""``translating_drf_refusals`` - an author callable, run inside ``translate_drf_refusals``."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast, overload

from django_service_specs.types.is_async import is_async
from typing_extensions import Format, get_annotations

from rest_framework_services.service_specs.translate_drf_refusals import translate_drf_refusals


@overload
def translating_drf_refusals(fn: None) -> None: ...


@overload
def translating_drf_refusals(fn: Callable[..., Any]) -> Callable[..., Any]: ...


def translating_drf_refusals(fn: Callable[..., Any] | None) -> Callable[..., Any] | None:
    """``fn``, its DRF refusals leaving as the kernel's classes; ``None`` for no callable.

    What the compile hands the kernel in place of each author callable that
    runs within a call: the service or selector, the preconditions, the
    ``kwargs`` and ``input_data`` providers and ``extend_queryset``. ``fn`` is
    called as it was, inside
    [`translate_drf_refusals`][rest_framework_services.service_specs.translate_drf_refusals.translate_drf_refusals].

    It keeps what a reader of the compiled spec takes from the callable: the
    name, qualname, module, annotations and signature, and, of a function or a
    method, the docstring and the attributes (a tool description is read off
    the docstring). An ``async def`` stays one, since the kernel awaits it by
    its kind (test_an_async_service_s_refusal_is_translated_too); the signature
    is ``fn``'s, which the kernel binds the call by; the annotations are
    ``fn``'s and ``__wrapped__`` is ``fn``, so a provider's return
    ``TypedDict`` is resolved in its own module and still names the keys it
    fills (``tests/service_specs/test_translating_drf_refusals.py``).

    Identity is not kept: the compiled callable is a wrapper, so
    ``inspect.unwrap`` is what reaches the author's own. A ``functools.partial``
    or a callable object gets neither docstring nor attributes, as its
    ``__doc__`` is its class's and a reader that tells a function from an
    object by ``inspect.isfunction`` would otherwise be handed that class's
    words. ``fn`` itself is never stamped, as it is the author's own function.
    """
    if fn is None:
        # A seat the spec leaves empty stays empty, so the kernel reads it as
        # it did (test_an_empty_seat_stays_empty).
        return None
    if is_async(fn):

        async def translating(*args: Any, **kwargs: Any) -> Any:
            with translate_drf_refusals():
                return await fn(*args, **kwargs)
    else:

        def translating(*args: Any, **kwargs: Any) -> Any:
            with translate_drf_refusals():
                return fn(*args, **kwargs)

    translating.__name__ = getattr(fn, "__name__", "callable")
    translating.__qualname__ = getattr(fn, "__qualname__", translating.__name__)
    translating.__module__ = getattr(fn, "__module__", None) or __name__
    # One arc to coverage, a disjunct each: test_the_compiled_callable_keeps_the_author_s_docstring
    # fails without ``isfunction`` for its function cases and without ``ismethod``
    # for its method cases, and test_a_partial_or_a_callable_object_gets_no_docstring_or_attributes
    # fails with the copy unconditional. Before ``__wrapped__`` and ``__signature__``
    # below, so a decorated function's own do not displace the translator's
    # (test_a_decorated_function_is_still_wrapped_by_the_translator).
    if inspect.isfunction(fn) or inspect.ismethod(fn):
        translating.__doc__ = fn.__doc__
        translating.__dict__.update(fn.__dict__)
    # Read without evaluating them: on 3.14 a name imported only for type
    # checking would raise here, at compile time, where the kernel's reader
    # leaves it unresolved (``provider_keys``).
    translating.__annotations__ = dict(get_annotations(fn, format=Format.FORWARDREF))
    cast(Any, translating).__wrapped__ = fn
    # Read once here rather than down ``__wrapped__`` on every call the kernel binds.
    cast(Any, translating).__signature__ = inspect.signature(fn)
    return translating
