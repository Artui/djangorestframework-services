"""``apply_input_data`` — merge server-provided keys onto a client payload."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import QueryDict


def apply_input_data(params: Any, extra: Mapping[str, Any]) -> Any:
    """Merge ``extra`` onto the client ``params``, server-provided keys winning.

    The canonical ``input_data`` merge for every transport. Three shapes:

    - A **list** (``many=True``) merges **per item** — the only coherent reading
      of "the server supplies these keys" for a batch, and what makes the
      ``input_data`` chain mean the same thing on the single and bulk paths.
    - A **QueryDict** (a form-encoded / multipart HTTP body) is copied and written
      through its own API. Its internal storage is ``{key: [values]}``, so
      dict-unpacking it would expose those value *lists* and turn every scalar
      into a one-element list — a ``ChoiceField`` seeing ``['X']`` →
      ``invalid_choice``. ``setlist`` for list/tuple values, plain assignment for
      scalars, matching how DRF's own serializers consume a QueryDict. Invisible
      to JSON-only tests, which is why it has a dedicated parity test.
    - Anything else merges by unpacking.

    Returns ``params`` **unchanged** when there is nothing to merge, so a body
    that never needed rewriting is never rewritten — the QueryDict reaches the
    serializer as itself.
    """
    if not extra:
        return params
    if isinstance(params, list):
        return [{**item, **extra} for item in params]
    if isinstance(params, QueryDict):
        merged = params.copy()
        for key, value in extra.items():
            if isinstance(value, (list, tuple)):
                merged.setlist(key, list(value))
            else:
                merged[key] = value
        return merged
    return {**params, **extra}


__all__ = ["apply_input_data"]
