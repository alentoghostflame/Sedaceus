from __future__ import annotations

from aiohttp import web
from logging import getLogger
from typing import Any, AsyncIterator, TYPE_CHECKING

import aiohttp
import asyncio
import uuid

from .enums import CoreEvents, EngineEvents, IPCClassType, IPCPayloadType
from ..core import DispatchFramework


if TYPE_CHECKING:
    from .engine import IPCEngine
    from .role import Role
    from .device import Device


__all__ = (
    "IPCConnection",
    "IPCCore",
    "IPCPacket",
)


logger = getLogger(__name__)


ConnDataTypes = str | dict | bytes | None


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
            data: ConnDataTypes,
            origin_conn_uuid: str | None = None,
            dest_conn_uuid: str | None = None,
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
        """Actual data to be transmitted in the packet."""
        self.origin_conn_uuid = origin_conn_uuid
        """Used to indicate what connection a specific packet is from."""
        self.dest_conn_uuid = dest_conn_uuid
        """UUID of the target connection, typically used to indicate that the specific connection should be modified."""
        self.event = event
        """Event that the destination should broadcast with the packet as the sole arg."""

    @classmethod
    def from_conn(
            cls,
            conn: IPCConnection,
            payload_type: IPCPayloadType,
            data: ConnDataTypes,
            event: str | None = None
    ) -> IPCPacket:
        ret = cls(
            payload_type=payload_type,
            origin_type=conn.origin_type,
            origin_name=conn.origin_name,
            origin_role=conn.origin_role,
            dest_type=conn.dest_type,
            dest_name=conn.dest_name,
            data=data,
            origin_conn_uuid=conn.uuid,
            dest_conn_uuid=conn.dest_conn_uuid,
            event=event
        )
        return ret

    def to_dict(self) -> dict:
        ret = {
            "type": self.type.value,
            "origin_type": self.origin_type.value,
            "origin_name": self.origin_name,
            "origin_role": self.origin_role,
            "destination_type": self.destination_type.value,
            "destination_name": self.destination_name,
            "data": self.data,
            "origin_conn_uuid": self.origin_conn_uuid,
            "dest_conn_uuid": self.dest_conn_uuid,
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
            origin_conn_uuid=packet["origin_conn_uuid"],
            dest_conn_uuid=packet["dest_conn_uuid"],
            event=packet["event"],
        )
        return ret


class IPCConnection:
    class ConnectionClosed(Exception):
        pass

    class CommunicationDenied(Exception):
        pass

    def __init__(
            self,
            engine: IPCEngine,
            conn: web.WebSocketResponse | aiohttp.ClientWebSocketResponse | None,
            origin_type: IPCClassType,
            origin_name: str,
            origin_role: str | None,
            dest_node: str,
            dest_type: IPCClassType,
            dest_name: str,
            dest_conn_uuid: str | None = None,
            uuid_override: str | None = None,
    ):
        self._engine = engine
        self._conn = conn
        self._origin_type = origin_type
        self._origin_name = origin_name
        self._origin_role = origin_role
        self._dest_node = dest_node
        self._dest_type = dest_type
        self._dest_name = dest_name
        self._dest_conn_uuid = dest_conn_uuid
        self._uuid = uuid_override or uuid.uuid1().hex

        # self._is_open: asyncio.Event = asyncio.Event()
        self._is_open: bool = False
        self._ready_to_continue: asyncio.Event = asyncio.Event()
        self._sent_comm_request: bool = False
        self._comm_denied: bool = False
        self._packet_queue: asyncio.Queue[IPCPacket | None] = asyncio.Queue()

    @property
    def origin_type(self) -> IPCClassType:
        return self._origin_type

    @property
    def origin_name(self) -> str:
        return self._origin_name

    @property
    def origin_role(self) -> str | None:
        return self._origin_role

    @property
    def dest_node(self) -> str:
        return self._dest_node

    @property
    def dest_type(self) -> IPCClassType:
        return self._dest_type

    @property
    def dest_name(self) -> str:
        return self._dest_name

    @property
    def dest_conn_uuid(self) -> str:
        return self._dest_conn_uuid

    @property
    def uuid(self) -> str:
        return self._uuid

    @property
    def is_open(self) -> bool:
        return self._is_open

    @classmethod
    def from_packet(cls, requestor: IPCCore, packet: IPCPacket, node_uuid: str) -> IPCConnection:
        origin_type, origin_name, origin_role = requestor.engine.map.get_requestor_info(requestor)
        conn = requestor.engine.map.resolve_node_conn(node_uuid)
        ret = cls(
            engine=requestor.engine,
            conn=conn,
            origin_type=origin_type,
            origin_name=origin_name,
            origin_role=origin_role,
            dest_node=node_uuid,
            dest_type=packet.origin_type,
            dest_name=packet.origin_name,
            dest_conn_uuid=packet.origin_conn_uuid,
        )
        return ret

    async def wait_until_ready(self):
        await self._ready_to_continue.wait()

    async def open(self):
        if self._comm_denied:
            raise self.CommunicationDenied("Communication request has been denied.")
        elif not self.is_open:
            # self._open = True
            self._engine.events.add_listener(self.on_ipc_communication, EngineEvents.COMMUNICATION)
            self._engine.events.add_listener(self._on_engine_close, EngineEvents.ENGINE_CLOSING)
            if self._sent_comm_request is False:
                await self._send_comm_request()
                self._sent_comm_request = True
        # else:
        #     raise ValueError("Connection is already open.")

    def force_open(self):
        self._engine.events.add_listener(self.on_ipc_communication, EngineEvents.COMMUNICATION)
        self._engine.events.add_listener(self._on_engine_close, EngineEvents.ENGINE_CLOSING)
        self._is_open = True
        self._ready_to_continue.set()

    async def close(self):
        if self.is_open:
            self._is_open = False
            self._ready_to_continue.clear()
            self._engine.events.remove_listener(self.on_ipc_communication, "ipc_communication")
            self._engine.events.remove_listener(self._on_engine_close, "engine_closing")
            await self._packet_queue.put(None)
        # else:
        #     raise ValueError("Connection is already closed.")

    async def change_dest(self, dest_node: str, dest_type: IPCClassType, dest_name: str | None):
        self._conn = self._engine.map.resolve_node_conn(dest_node)
        self._dest_node = dest_node
        self._dest_type = dest_type
        self._dest_name = dest_name

    async def _on_engine_close(self):
        await self.close()

    async def send_com(self, data: ConnDataTypes):
        packet = IPCPacket.from_conn(self, IPCPayloadType.COMMUNICATION, data=data)
        await self.send_packet(packet)

    async def _send_comm_request(self):
        comm_request = IPCPacket.from_conn(self, IPCPayloadType.COMMUNICATION_REQUEST, None)
        await self.send_packet(comm_request, ignore_checks=True)

    async def send_packet(self, packet: IPCPacket, ignore_checks: bool = False):
        if not ignore_checks and not self.is_open:
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
        if not self.is_open:
            await self.open()

        await self._ready_to_continue.wait()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.is_open:
            await self.close()

    def __aiter__(self) -> AsyncIterator[IPCPacket]:
        return self

    async def __anext__(self) -> IPCPacket:
        if self.is_open:
            try:
                return await self.receive()
            except self.ConnectionClosed:
                raise StopAsyncIteration

        else:
            raise StopAsyncIteration

    async def on_ipc_communication(self, packet: IPCPacket, origin_node: str | None):
        if packet.dest_conn_uuid == self.uuid:
            if self.is_open:
                if packet.type is IPCPayloadType.COMMUNICATION:
                    await self._packet_queue.put(packet)
            elif self._comm_denied is False:
                match packet.type:
                    case IPCPayloadType.COMMUNICATION_ACCEPTED:
                        self._is_open = True
                        self._dest_conn_uuid = packet.origin_conn_uuid
                        self._ready_to_continue.set()
                    case IPCPayloadType.COMMUNICATION_DENIED:
                        self._comm_denied = True
                        self._ready_to_continue.set()
                    case IPCPayloadType.COMMUNICATION_REDIRECT:
                        dest_type = packet.data["destination_type"]
                        dest_name = packet.data["destination_name"]
                        dest_node = packet.data["destination_node"]
                        data = packet.data["original_data"]
                        await self.change_dest(dest_node, dest_type, dest_name)
                        await self.send_com(data)


class IPCCore:
    engine: IPCEngine

    def __init__(self):
        self.events = DispatchFramework()

        self.events.add_listener(self.on_incoming_connection, CoreEvents.INCOMING_CONNECTION)
        self.events.add_listener(self.on_connection, CoreEvents.CONNECTION)

    async def on_incoming_connection(self, packet: IPCPacket, node_uuid: str):
        """Ran when an incoming connection is established but needs to be accepted."""
        logger.debug("Received incoming connection from node %s.", node_uuid)
        if await self.accept_incoming_connection(packet, node_uuid):
            logger.debug("Incoming connection locally accepted. Creating conn, sending accept, and dispatching.")
            conn = IPCConnection.from_packet(self, packet, node_uuid)
            accept_packet = IPCPacket.from_conn(conn, IPCPayloadType.COMMUNICATION_ACCEPTED, packet.origin_conn_uuid)
            await conn.send_packet(accept_packet, True)
            conn.force_open()
            self.events.dispatch(CoreEvents.CONNECTION, conn)
        else:
            logger.debug("Incoming connection locally denied. Creating conn, sending deny, and ignoring.")
            conn = IPCConnection.from_packet(self, packet, node_uuid)
            deny_packet = IPCPacket.from_conn(conn, IPCPayloadType.COMMUNICATION_DENIED, conn.uuid)
            await conn.send_packet(deny_packet, True)

    # noinspection PyMethodMayBeStatic
    async def accept_incoming_connection(self, packet: IPCPacket, node_uuid: str) -> bool:
        """Used for custom logic for if a connection should be accepted or not. Return True to accept it, False to
        deny the connection.
        """
        return True

    async def on_connection(self, conn: IPCConnection):
        """Ran when an incoming connection is established and accepted."""
        raise NotImplementedError
