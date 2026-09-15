"""A project's own pool seeds, end to end through both dispatch cores.

Each test here names an asymmetry that existed before ``pool_seeds=``: a seed an
adapter added through ``base_pool(**extra)`` reached the callable, and then got
**neither** of the two protections the seven built-ins get — client input was not
stripped from the spread, and the name still read as an unexpected argument.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.unknown_arguments import UnknownArguments
from tests.testapp.models import Post

TENANT_SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda *, user: f"tenant-of-{user}")


def _titles(value: Any) -> list[str]:
    return sorted(post.title for post in value)


@pytest.mark.django_db
def test_a_registered_seed_reaches_a_selector_that_declares_it() -> None:
    """Without a registry there is no channel at all: the call raises ``TypeError``."""
    Post.objects.create(title="p")
    seen: dict[str, Any] = {}

    def selector(*, tenant: str) -> Any:
        seen["tenant"] = tenant
        return Post.objects.all()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
    result = dispatch_spec(spec, user="u", params={}, pool_seeds=TENANT_SEEDS)

    assert seen == {"tenant": "tenant-of-u"}
    assert _titles(result.value) == ["p"]


@pytest.mark.django_db
def test_a_registered_seed_outranks_client_input_of_the_same_name() -> None:
    """The asymmetry this feature closes.

    A spread on a selector has no validator in front of it, so before ``pool_seeds=``
    a caller-supplied ``tenant`` landed in the pool and the callable could not tell
    it from the project's own value.
    """
    Post.objects.create(title="p")
    seen: dict[str, Any] = {}

    def selector(*, tenant: str) -> Any:
        seen["tenant"] = tenant
        return Post.objects.all()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
    dispatch_spec(
        spec,
        user="u",
        params={"tenant": "supplied-by-the-caller"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        pool_seeds=TENANT_SEEDS,
    )

    assert seen == {"tenant": "tenant-of-u"}


@pytest.mark.django_db
def test_a_registered_seed_name_is_not_an_unexpected_argument() -> None:
    """The second protection, and the one a reader is least likely to predict.

    ``REJECT`` exempts the built-in seed names; a registered name has to be
    exempt for the same reason, or declaring a seed makes the strictest input
    policy start refusing calls.
    """
    Post.objects.create(title="p")

    def selector(*, tenant: str) -> Any:
        return Post.objects.all()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
    result = dispatch_spec(
        spec,
        user="u",
        params={"tenant": "ignored"},
        unknown_arguments=UnknownArguments.REJECT,
        pool_seeds=TENANT_SEEDS,
    )

    assert _titles(result.value) == ["p"]


@pytest.mark.django_db
def test_registering_nothing_leaves_every_path_unchanged() -> None:
    """The default is the empty registry, so an existing caller sees no difference."""
    Post.objects.create(title="p")
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: Post.objects.all())

    assert _titles(dispatch_spec(spec, user="u", params={}).value) == ["p"]


@pytest.mark.django_db(transaction=True)
async def test_the_async_core_seeds_the_pool_the_same_way() -> None:
    """``adispatch_spec`` is the other half of the parity rule."""
    await Post.objects.acreate(title="p")
    seen: dict[str, Any] = {}

    def selector(*, tenant: str) -> Any:
        seen["tenant"] = tenant
        return Post.objects.all()

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
    await adispatch_spec(
        spec,
        user="u",
        params={"tenant": "supplied-by-the-caller"},
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        pool_seeds=TENANT_SEEDS,
    )

    assert seen == {"tenant": "tenant-of-u"}
