from __future__ import annotations

from typing import Any, TYPE_CHECKING

import asyncio
import pickle

from .core import HTTPOverWSRequest, HTTPOverWSResponse, InboundConnection, OutboundConnection

from ..core import DispatchFramework


if TYPE_CHECKING:
    from .engine import IPCEngine, DeviceRole, IPCDevice


class LocalConnection:
    class ConnectionClosing(Exception):
        pass

    def __init__(self, target: LocalConnection = None):
        self._target: LocalConnection | None = target

        self._local_queue: asyncio.Queue = asyncio.Queue()
        self._connection_task: asyncio.Task | None = None
        self._closing: asyncio.Event = asyncio.Event()
        self._connected: bool = False
        self._events: DispatchFramework = DispatchFramework()

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, target: LocalConnection = None):
        if target:
            self._target = target

        self._connected = True

    async def send(self, data):
        await self._target._local_queue.put(data)

    async def send_pickled(self, data, force_pickle=True):
        if force_pickle:
            data = pickle.dumps(data)

        await self.send(data)

    async def send_httplike(self, data: Any) -> HTTPOverWSRequest:
        packet = HTTPOverWSRequest(data)
        await self.send_pickled(packet)
        return packet

    async def receive(self) -> Any:
        ret = await self._local_queue.get()
        self._local_queue.task_done()
        if self._closing.is_set():
            raise self.ConnectionClosing("The local connection is now closed.")

        if isinstance(ret, bytes):
            ret = pickle.loads(ret)
            if isinstance(ret, HTTPOverWSRequest):
                ret._connection = self

        self._events.dispatch("data", ret)
        return ret

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __aiter__(self) -> LocalConnection:
        return self

    async def __anext__(self) -> Any:
        if self._connected:
            try:
                return await self.receive()
            except self.ConnectionClosing:
                raise StopAsyncIteration

        else:
            raise StopAsyncIteration

    async def close(self):
        self._connected = False
        self._closing.set()
        await self._local_queue.put(None)


class LocalInboundConnection(InboundConnection, LocalConnection):
    def __init__(self, outbound_conn: LocalOutboundConnection):
        LocalConnection.__init__(self, outbound_conn)
        self.source_uuid = outbound_conn._source_device.uuid
        self.source_role = outbound_conn._source_device._role

    async def send(self, data):
        return await LocalConnection.send(self, data)

    async def send_pickled(self, data, force_pickle=True):
        return await LocalConnection.send_pickled(self, data, force_pickle=True)

    async def send_httplike(self, data):
        return await LocalConnection.send_httplike(self, data)

    async def receive(self) -> Any:
        return await LocalConnection.receive(self)

    async def close(self):
        return await LocalConnection.close(self)

    async def __aenter__(self):
        return await LocalConnection.__aenter__(self)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await LocalConnection.__aexit__(self, exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return LocalConnection.__aiter__(self)

    async def __anext__(self):
        return await LocalConnection.__anext__(self)


class LocalOutboundConnection(OutboundConnection, LocalConnection):
    def __init__(self, engine: IPCEngine, source_device: IPCDevice):
        OutboundConnection.__init__(self)
        LocalConnection.__init__(self)
        self._engine: IPCEngine = engine
        self._source_device: IPCDevice = source_device

    async def connect(self, target_role: DeviceRole):
        inbound_conn = LocalInboundConnection(self)
        await LocalConnection.connect(self, inbound_conn)
        await inbound_conn.connect(self)
        await target_role.on_local_ws_request(inbound_conn)

    async def send(self, data):
        return await LocalConnection.send(self, data)

    async def send_pickled(self, data, force_pickle=True):
        return await LocalConnection.send_pickled(self, data, force_pickle=True)

    async def send_httplike(self, data):
        return await LocalConnection.send_httplike(self, data)

    async def receive(self) -> Any:
        return await LocalConnection.receive(self)

    async def close(self):
        return await LocalConnection.close(self)

    async def __aenter__(self):
        return await LocalConnection.__aenter__(self)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await LocalConnection.__aexit__(self, exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return LocalConnection.__aiter__(self)

    async def __anext__(self):
        return await LocalConnection.__anext__(self)
