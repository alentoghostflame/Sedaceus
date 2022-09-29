from __future__ import annotations

import uuid

from logging import getLogger
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    from .engine import DeviceRole


# WS_ROLE_PATH = "/ws/role/{}"


# TODO: Make connection pool object that dictates how connections work? Redundancy/Fallback, multiple connections, etc?
# TODO: Make connection object that says if something is offline or online? Method that makes a connection?


logger = getLogger(__name__)


class HTTPOverWSRequest:
    def __init__(
            self,
            data: Any,
            *,
            uuid_override: str | None = None,
            connection: InboundConnection | OutboundConnection = None,
    ):
        self.data: Any = data
        self.uuid = uuid_override or uuid.uuid1().hex
        self._connection: InboundConnection | OutboundConnection | None = connection

    async def reply(self, data: Any):
        if self._connection:
            packet = HTTPOverWSResponse(data, self.uuid)
            await self._connection.send_pickled(packet)
        else:
            raise ValueError("No connection was provided when initializing, cannot reply!")


class HTTPOverWSResponse:
    def __init__(self, data: Any, request_uuid: str):
        self.data: Any = data
        self.uuid = request_uuid


class InboundConnection:
    connected: bool
    source_uuid: str
    source_role: DeviceRole

    async def send(self, data) -> None:
        raise NotImplementedError

    async def send_pickled(self, data, force_pickle=False) -> None:
        raise NotImplementedError

    async def send_httplike(self, data) -> HTTPOverWSRequest:
        raise NotImplementedError

    async def receive(self) -> Any:
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError

    async def __aenter__(self):
        raise NotImplementedError

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        raise NotImplementedError

    def __aiter__(self):
        raise NotImplementedError

    async def __anext__(self):
        raise NotImplementedError


class OutboundConnection:
    connected: bool
    target_role: DeviceRole

    async def connect(self, target_role: DeviceRole) -> None:
        raise NotImplementedError

    async def send(self, data) -> None:
        raise NotImplementedError

    async def send_pickled(self, data, force_pickle=False) -> None:
        raise NotImplementedError

    async def send_httplike(self, data) -> HTTPOverWSRequest:
        raise NotImplementedError

    async def receive(self) -> Any:
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError

    async def __aenter__(self):
        raise NotImplementedError

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        raise NotImplementedError

    def __aiter__(self):
        raise NotImplementedError

    async def __anext__(self):
        raise NotImplementedError
