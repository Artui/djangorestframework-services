"""``ChangeResult`` is django-service-specs' own, re-exported at drfs' path.

``ModelT`` comes with it, from the same kernel module: it is the TypeVar the
class is generic in, which the model write helpers here annotate with and
``tests/mutations/test_typing.py`` reads back off the class.
"""

from __future__ import annotations

from django_service_specs.mutations.change_result import ChangeResult, ModelT

__all__ = ["ChangeResult", "ModelT"]
