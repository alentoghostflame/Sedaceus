from __future__ import annotations

from aiohttp import web
from logging import getLogger
from typing import Any, AsyncIterator, TYPE_CHECKING

import aiohttp
import asyncio

from .enums import EngineEvents, IPCClassType, IPCPayloadType


if TYPE_CHECKING:
    from .engine import IPCEngine


__all__ = (
    "IPCPacket",
    "IPCConnection",
)


logger = getLogger(__name__)


class IPCPacket:
    def __init__(
            self,
            *,
            payload_type: IPCPayloadType,
            origin_type: IPCClassType,
            origin_name: str | None,
            origin_role: str | None,
            dest_type: IPCClassType,
            dest_name: str | None,
            data: str | dict | bytes,
            event: str | None = None,
    ):
        self.type = payload_type
        self.origin_type = origin_type
        """IPC Class Type that the packet is being sent from."""
        self.origin_name = origin_name
        """Role name for Role origin, Device UUID for Device origin, node UUID for Engine origin."""
        self.origin_role = origin_role
        """Role name of the Device for Device origin. None for Role and Engine origin."""
        self.destination_type = dest_type
        """IPC Class Type that the packet is being sent to."""
        self.destination_name = dest_name
        """Role name for Role origin, Device UUID for Device origin, node UUID for Engine origin."""
        self.data = data
        self.event = event

    def to_dict(self) -> dict:
        ret = {
            "type": self.type.value,
            "origin_type": self.origin_type.value,
            "origin_name": self.origin_name,
            "origin_role": self.origin_role,
            "destination_type": self.destination_type.value,
            "destination_name": self.destination_name,
            "data": self.data,
            "event": self.event,
        }
        return ret

    @classmethod
    def from_dict(cls, packet: dict) -> IPCPacket:
        ret = cls(
            payload_type=IPCPayloadType(packet["type"]),
            origin_type=IPCClassType(packet["origin_type"]),
            origin_name=packet["origin_name"],
            origin_role=packet["origin_role"],
            dest_type=IPCClassType(packet["destination_type"]),
            dest_name=packet["destination_name"],
            data=packet["data"],
            event=packet["event"],
        )
        return ret


class IPCConnection:
    class ConnectionClosed(Exception):
        pass

    def __init__(
            self,
            engine: IPCEngine,
            conn: web.WebSocketResponse | aiohttp.ClientWebSocketResponse | None,
            origin_type: IPCClassType,
            origin_name: str | None,
            origin_role: str | None,
            dest_node: str | None,
            dest_type: IPCClassType,
            dest_name: str | None,
            # attempt_reconnect: bool = True
    ):
        self._engine = engine
        self._conn = conn
        self._origin_type = origin_type
        self._origin_name = origin_name
        self._origin_role = origin_role
        self._dest_node = dest_node
        self._dest_type = dest_type
        self._dest_name = dest_name
        # self._attempt_reconnect = attempt_reconnect

        self._open: bool = False

        # self._waiting_for_reconnect: asyncio.Event = asyncio.Event()
        # self._waiting_for_reconnect.set()
        self._packet_queue: asyncio.Queue[IPCPacket | None] = asyncio.Queue()

    @property
    def is_open(self) -> bool:
        return self._open

    async def open(self):
        if self._open is False:
            self._open = True
            self._engine.events.add_listener(self.on_ipc_communication, EngineEvents.COMMUNICATION)
            self._engine.events.add_listener(self._on_engine_close, EngineEvents.ENGINE_CLOSING)

    async def close(self):
        if self._open is True:
            self._open = False
            self._engine.events.remove_listener(self.on_ipc_communication, "ipc_communication")
            self._engine.events.remove_listener(self._on_engine_close, "engine_closing")
            await self._packet_queue.put(None)

    async def _on_engine_close(self):
        await self.close()

    async def send_com(self, data: Any):
        # if not self.is_open:
        #     raise self.ConnectionClosed("The IPC connection has been closed.")

        packet = IPCPacket(
            payload_type=IPCPayloadType.COMMUNICATION,
            origin_type=self._origin_type,
            origin_name=self._origin_name,
            origin_role=self._origin_role,
            dest_type=self._dest_type,
            dest_name=self._dest_name,
            data=data
        )
        await self.send_packet(packet)
        # if self._conn is None:
        #     self._engine.events.dispatch("ipc_communication", packet, self._origin_name)
        # else:
        #     try:
        #         await self._conn.send_json(packet.to_dict())
        #     except Exception as e:
        #         logger.debug(
        #             "%s error when communicating with node %s, closing connection.",
        #             e.__class__,
        #             self._dest_node
        #         )
        #         await self.close()
        #         raise self.ConnectionClosed("The IPC connection has been closed.")

    async def send_packet(self, packet: IPCPacket):
        if not self.is_open:
            raise self.ConnectionClosed("The IPC connection has been closed.")

        if self._conn is None:
            self._engine.events.dispatch("ipc_communication", packet, self._origin_name)
        else:
            try:
                await self._conn.send_json(packet.to_dict())
            except Exception as e:
                logger.debug(
                    "%s error when communicating with node %s, closing connection.",
                    e.__class__,
                    self._dest_node
                )
                await self.close()
                raise self.ConnectionClosed("The IPC connection has been closed.")

    async def receive(self) -> IPCPacket | None:
        if self._conn is not None and self._conn.closed:
            raise self.ConnectionClosed("The IPC connection has been closed.")

        ret = await self._packet_queue.get()
        self._packet_queue.task_done()
        if ret is None:
            raise self.ConnectionClosed("The IPC connection has been closed.")

        return ret

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __aiter__(self) -> AsyncIterator[IPCPacket]:
        return self

    async def __anext__(self) -> IPCPacket:
        if self._open:
            try:
                return await self.receive()
            except self.ConnectionClosed:
                raise StopAsyncIteration

        else:
            raise StopAsyncIteration

    async def on_ipc_communication(self, packet: IPCPacket, origin_node: str | None):
        # logger.debug(
        #     "Packet type: %s, dest_type: %s, dest_name: %s\n Our dest_type: %s, our dest_name: %s",
        #     packet.type, packet.destination_type, packet.destination_name, self._origin_type, self._origin_name
        # )
        if (
                packet.type is IPCPayloadType.COMMUNICATION and
                packet.destination_type is self._origin_type and
                packet.destination_name == self._origin_name
        ):
            await self._packet_queue.put(packet)
