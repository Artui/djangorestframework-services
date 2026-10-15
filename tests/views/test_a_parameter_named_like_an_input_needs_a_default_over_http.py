"""Over HTTP, a service parameter named like an input field or a read needs a default.

Dispatch hands a service each validated value, and each declared read, by
name. ``as_view()`` checks a callable's required parameters before any call,
against the keys every call provides, and neither is one of them: a ``PATCH``
may leave the field out, and a request may carry no query value. So a
required parameter of either name is refused when the view is built, and one
with a default is handed the value the call sends.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    QueryParam,
    ServiceCreateView,
    ServiceSpec,
    dispatch_spec,
)


class _In(serializers.Serializer):
    title = serializers.CharField()


def _titled(*, data: Any, title: str) -> dict[str, Any]:
    return {"title": title}


def _titled_or_none(*, data: Any, title: str | None = None) -> dict[str, Any]:
    return {"title": title}


def _scoped(*, data: Any, scope: str) -> dict[str, Any]:
    return {"scope": scope}


def _scoped_or_none(*, data: Any, scope: str | None = None) -> dict[str, Any]:
    return {"scope": scope}


_READ = (QueryParam("scope"),)
_REQUIRED = {"field": (_titled, (), "title"), "read": (_scoped, _READ, "scope")}
_DEFAULTED = {"field": (_titled_or_none, ()), "read": (_scoped_or_none, _READ)}


def _spec(service: Any, reads: tuple[QueryParam, ...]) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=service,
        input_serializer=_In,
        reads=reads,
        permission_classes=[],
        atomic=False,
    )


def _view(spec: ServiceSpec[Any, Any, Any]) -> Any:
    return type("_View", (ServiceCreateView,), {"spec": spec, "permission_classes": []})


@pytest.mark.parametrize("named", list(_REQUIRED))
def test_with_no_default_it_is_refused_when_the_view_is_built(named: str) -> None:
    service, reads, name = _REQUIRED[named]

    with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\] that the framework does not"):
        _view(_spec(service, reads)).as_view()


@pytest.mark.django_db
@pytest.mark.parametrize("named", list(_REQUIRED))
def test_with_no_default_it_is_handed_the_value_off_http(named: str) -> None:
    service, reads, name = _REQUIRED[named]

    result = dispatch_spec(_spec(service, reads), user=None, params={"title": "a", "scope": "s"})

    assert result.value == {name: {"title": "a", "scope": "s"}[name]}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("named", "expected"), [("field", {"title": "a"}), ("read", {"scope": "s"})]
)
def test_with_a_default_it_is_handed_the_value_over_http(
    named: str, expected: dict[str, Any]
) -> None:
    service, reads = _DEFAULTED[named]
    request = APIRequestFactory().post("/?scope=s", {"title": "a"}, format="json")

    response = _view(_spec(service, reads)).as_view()(request)

    assert response.status_code == 201
    assert response.data == expected
