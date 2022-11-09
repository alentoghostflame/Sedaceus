from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

import uuid

from ..core import DispatchFramework


if TYPE_CHECKING:
    from .engine import IPCEngine
    from .device import Device


__all__ = (
    "Role",
)


logger = getLogger(__name__)


class Role:
    def __init__(self, name: str):
        self.events: DispatchFramework = DispatchFramework()
        # self._uuid: str = uuid_override or uuid.uuid1().hex
        self._name: str = name
        self._engine: IPCEngine | None = None
        """Set with the Role is added to an Engine."""
        self._devices: dict[str, Device] = {}
        """{"Device UUID": Device}"""

    @property
    def name(self) -> str:
        return self._name

    @property
    def engine(self) -> IPCEngine | None:
        return self._engine

    def set_engine(self, engine: IPCEngine):
        self._engine = engine
        for device in self._devices.values():
            if device.engine is None:
                device.set_engine(engine)

    @property
    def devices(self) -> dict[str, Device]:
        return self._devices.copy()

    def add_device(self, device: Device):
        if device.uuid in self._devices and self._devices[device.uuid] is not device:
            raise ValueError(f"Device with UUID {device.uuid} has already been added to this role.")

        self._devices[device.uuid] = device
        if self._engine is not None:
            device.set_engine(self.engine)

        device.set_role(self)
