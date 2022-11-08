from __future__ import annotations

from typing import Any, TYPE_CHECKING

import aiohttp
import asyncio
import pickle

from .core import HTTPOverWSRequest, HTTPOverWSResponse, InboundConnection, OutboundConnection

from ..core import DispatchFramework


if TYPE_CHECKING:
    from .engine import IPCEngine, DeviceRole, IPCDevice


__all__ = (
    "WebsocketInboundConnection",
    "WebsocketOutboundConnection"
)


class WebsocketInboundConnection(InboundConnection):
    pass


class WebsocketOutboundConnection(OutboundConnection):
    def __init__(self, engine: IPCEngine, source_device: IPCDevice):
        OutboundConnection.__init__(self)
        self.target_role = None

        self._engine: IPCEngine = engine
        self._source_device: IPCDevice = source_device
        self._session: aiohttp.ClientSession = engine.session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ready_for_data: asyncio.Event = asyncio.Event()
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, target_role: DeviceRole) -> None:
        self.target_role = target_role
        target_url = target_role.get_connection_url(self._source_device.uuid, self._source_device._role)
        if target_url is None:
            raise ValueError("Role to connect to is specifying to connect locally.")

        self._ws = await self._session.ws_connect(
            url=target_url, heartbeat=3.0, headers={"ipc_source_role": self._source_device._role.name}
        )

    async def send(self, data) -> None:
        await self._ready_for_data.wait()


    async def send_pickled(self, data, force_pickle=False) -> None:
        pass

    async def send_httplike(self, data) -> HTTPOverWSRequest:
        pass

    async def receive(self) -> Any:
        await self._ready_for_data.wait()

    async def close(self):
        pass

    async def __aenter__(self):
        await self._ready_for_data.wait()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def __aiter__(self):
        pass

    async def __anext__(self):
        pass
