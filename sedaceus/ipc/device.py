from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

import uuid

from ..core import DispatchFramework


if TYPE_CHECKING:
    from .engine import IPCEngine
    from .role import Role


__all__ = (
    "Device",
)


logger = getLogger(__name__)


class Device:
    def __init__(self, uuid_override: str | None = None):
        self.events: DispatchFramework = DispatchFramework()
        self._uuid: str = uuid_override or uuid.uuid1().hex
        self._engine: IPCEngine | None = None
        """Set when the Device is added to an Engine."""
        self._role: Role | None = None
        """Set when the Device is added to a Role."""

    @property
    def role(self) -> Role | None:
        return self._role

    def set_role(self, role: Role):
        self._role = role

    @property
    def engine(self) -> IPCEngine | None:
        return self._engine

    def set_engine(self, engine: IPCEngine):
        self._engine = engine

    @property
    def uuid(self) -> str:
        return self._uuid