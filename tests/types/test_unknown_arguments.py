"""``UnknownArguments``: what a drfs caller sees change now that it is the kernel's.

The enum is django-service-specs' and its policies are applied there. It keeps
``PASSTHROUGH`` as a name, so a published transport comparing a policy against
it by identity still finds the attribute; refusing it as a policy is the
dispatch edge's, and tested there.
"""

from __future__ import annotations

from rest_framework_services.types.unknown_arguments import UnknownArguments


def test_every_member_drfs_named_is_still_there() -> None:
    assert {member.name for member in UnknownArguments} == {"REJECT", "IGNORE", "PASSTHROUGH"}


def test_a_member_compares_equal_to_its_string() -> None:
    """A ``str`` enum, so a policy read from settings as ``"ignore"`` is the
    member it spells."""
    assert UnknownArguments.IGNORE == "ignore"
    assert UnknownArguments.REJECT == "reject"


def test_the_members_are_listed_strictest_first() -> None:
    assert list(UnknownArguments) == [
        UnknownArguments.REJECT,
        UnknownArguments.IGNORE,
        UnknownArguments.PASSTHROUGH,
    ]
