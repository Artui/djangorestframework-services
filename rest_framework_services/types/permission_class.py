from __future__ import annotations

from typing import TypeAlias

from rest_framework.permissions import BasePermission, OperandHolder, SingleOperandHolder

# One entry of a spec's ``permission_classes``: a ``BasePermission`` subclass, or
# the operand DRF builds from them with ``|``, ``&`` and ``~``. That is the union
# ``APIView.permission_classes`` takes, so a rule written for a view reads the
# same on a spec. An operand is not a class -- ``IsStaff | HasKey`` is an
# ``OperandHolder`` instance -- but every consumer only calls it, and calling an
# operand returns the composed permission instance, just as calling a class does.
PermissionClass: TypeAlias = type[BasePermission] | OperandHolder | SingleOperandHolder
