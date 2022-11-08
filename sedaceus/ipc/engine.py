from __future__ import annotations

import aiohttp
import asyncio
import pickle
import uuid

from aiohttp import web
from enum import Enum, IntEnum, unique
from logging import getLogger
from typing import Any, AsyncIterator, Coroutine, Type

from .core import InboundConnection, OutboundConnection, HTTPOverWSRequest, HTTPOverWSResponse
from .local import LocalOutboundConnection

from ..core import DispatchFramework, listen


# WS_ROLE_PATH = "/ws/role/{}"


# TODO: Make connection pool object that dictates how connections work? Redundancy/Fallback, multiple connections, etc?
# TODO: Make connection object that says if something is offline or online? Method that makes a connection?


logger = getLogger(__name__)


CONNECT_RETRY_SLEEP = 10  # Time in seconds.
ENGINE_DISCOVERY_ROUTE = "discovery"
ENGINE_IPC_ROUTE = "engine/ipc"


@unique
class IPCPayloadType(IntEnum):
    COMMUNICATION = 0
    """Valid destination: ENGINE, ROLE, DEVICE
    
    
    Packet data: IPCPacket dictionary
    """
    DISCOVERY = 1
    """Valid destination: ENGINE
    
    Packet data: "Node UUID"
    """
    ROLE_ADD = 2
    """Valid destination: ENGINE

    Packet data: "Role name here"
    """
    ROLE_REMOVE = 3
    """Valid destination: ENGINE
    
    Packet data: "Role name here"
    """
    DEVICE_ADD = 4
    """Valid destination: ENGINE
    
    Packet data: {"uuid": "Device UUID Here", "role": "Role name here"}
    """
    DEVICE_REMOVE = 5
    """Valid destination: ENGINE

    Packet data: {"uuid": "Device UUID Here", "role": "Role name here"}
    """


@unique
class IPCClassType(IntEnum):
    ENGINE = 0
    ROLE = 1
    DEVICE = 2


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
            self._engine.events.add_listener(self.on_ipc_communication, IPCEngine.Events.COMMUNICATION)
            self._engine.events.add_listener(self._on_engine_close, IPCEngine.Events.ENGINE_CLOSING)

    async def close(self):
        if self._open is True:
            self._open = False
            self._engine.events.remove_listener(self.on_ipc_communication, "ipc_communication")
            self._engine.events.remove_listener(self._on_engine_close, "engine_closing")
            await self._packet_queue.put(None)

    async def _on_engine_close(self):
        await self.close()

    async def send(self, data: Any):
        if not self.is_open:
            raise self.ConnectionClosed("The IPC connection has been closed.")

        packet = IPCPacket(
            payload_type=IPCPayloadType.COMMUNICATION,
            origin_type=self._origin_type,
            origin_name=self._origin_name,
            origin_role=self._origin_role,
            dest_type=self._dest_type,
            dest_name=self._dest_name,
            data=data
        )
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


class ConnectionMap:

    class MISSING:
        pass

    def __init__(self, engine: IPCEngine):
        self._engine = engine

        self._remote_nodes: dict[str, web.WebSocketResponse | aiohttp.ClientWebSocketResponse] = {}
        """{"Node UUID": (Client)WebSocketResponse object}"""
        self._remote_roles: dict[str, list[str]] = {}
        """{"Role Name": ["Node UUID 1 with role", "Node UUID 2 with role", ...]}"""
        self._remote_devices: dict[str, str] = {}
        """{"Device UUID": "Node UUID"}"""
        # TODO: Remote devices need to have their role associated with them. If a role gets added AFTER devices in a
        #  different node were synced, then local role wont know all devices across the network with that role.
        #  {"node_uuid": "foo", "role": "role_name"}

    @property
    def remote_nodes(self) -> dict[str, web.WebSocketResponse | aiohttp.ClientWebSocketResponse]:
        return self._remote_nodes.copy()

    @property
    def remote_roles(self) -> dict[str, list[str]]:
        return self._remote_roles.copy()

    @property
    def remote_devices(self) -> dict[str, str]:
        return self._remote_devices.copy()

    @property
    def local_roles(self) -> set:
        # return self._local_roles.copy()
        return set(self._engine._roles.keys())

    @property
    def local_devices(self) -> set:
        ret = set()
        for role in self._engine._roles.values():
            for device in role.devices.values():
                ret.add(device.uuid)

        return ret

    @property
    def nodes(self) -> list[str]:
        return list(self.remote_nodes.keys()) + [self._engine.uuid, ]

    @property
    def roles(self) -> list[str]:
        return list(self.local_roles) + list(self._remote_roles.keys())

    @property
    def devices(self) -> list[str]:
        return list(self.local_devices) + list(self._remote_devices.keys())

    def clean(self):
        for node_uuid, ws in self.remote_nodes.items():
            if ws.closed:
                self.remove_node(node_uuid)

    def add_node(self, ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse, node_uuid: str) -> bool:
        if node_uuid in self._remote_nodes:
            return False
        else:
            self._remote_nodes[node_uuid] = ws
            return True

    def remove_node(self, rm_node_uuid: str) -> bool:
        if rm_node_uuid not in self._remote_nodes:
            return False

        logger.debug("Fully removing node %s from NodeMap.", rm_node_uuid)

        # Removes the node UUID from the dict of remote roles.
        for node_list in self._remote_roles.values():
            if rm_node_uuid in node_list:
                node_list.remove(rm_node_uuid)

        # Removes devices from the dict if the node they were attached to is no longer connected.
        for device_uuid, remote_node_uuid in self.remote_devices:
            if remote_node_uuid == rm_node_uuid:
                logger.debug("Removing device %s", device_uuid)
                self._remote_devices.pop(device_uuid)

        # Finally, removes the node UUID and websocket object for it from the dict.
        self._remote_nodes.pop(rm_node_uuid)

        return True

    # def add_role(self, role_name: str, origin: str | None) -> bool:
    #     if origin is None:
    #         if role_name in self._local_roles:
    #             return False
    #         else:
    #             self._local_roles.add(role_name)
    #             return True
    #     else:
    #         if role_name in self._remote_roles and origin in self._remote_roles[role_name]:
    #             return False
    #         else:
    #             if role_name not in self._remote_roles:
    #                 self._remote_roles[role_name] = []
    #
    #             self._remote_roles[role_name].append(role_name)
    #             return True
    #
    # def add_device(self, device_uuid: str, origin: str | None) -> bool:
    #     if origin is None:
    #         if device_uuid in self._local_devices:
    #             return False
    #         else:
    #             self._local_devices.add(device_uuid)
    #             return True
    #     else:
    #         if device_uuid in self._remote_devices:
    #             return False
    #         else:
    #             self._remote_devices[device_uuid] = origin
    #             return True

    def resolve_node_conn(self, node_uuid: str) -> aiohttp.ClientWebSocketResponse | web.WebSocketResponse | None:
        if node_uuid == self._engine.uuid:
            return None
        elif node_uuid in self._remote_nodes:
            return self._remote_nodes[node_uuid]
        else:
            raise ValueError(f"Node with UUID {node_uuid} not found.")

    def resolve_role_conn(
            self,
            role_name: str,
            prefer_self: bool = True
    ) -> tuple[str, aiohttp.ClientWebSocketResponse | web.WebSocketResponse | None]:
        if prefer_self and role_name in self.local_roles:
            return self._engine.uuid, None
        elif role_name in self.remote_roles and len(self.remote_roles[role_name]) > 0:
            node_uuid = self.remote_roles[role_name][0]
            return node_uuid, self.resolve_node_conn(node_uuid)
        else:
            raise ValueError(f"No nodes with Role {role_name} found.")

    def resolve_device_conn(
            self,
            device_uuid: str,
    ) -> tuple[str, aiohttp.ClientWebSocketResponse | web.WebSocketResponse | None]:
        if device_uuid in self.local_devices:
            return self._engine.uuid, None
        elif node_uuid := self._remote_devices.get(device_uuid):
            return node_uuid, self._remote_nodes[node_uuid]
        else:
            raise ValueError(f"Device with UUID {device_uuid} not found.")

    def resolve_conn(
            self,
            uuid_or_name: str,
            prefer_self: bool = True,
    ) -> tuple[str, aiohttp.ClientWebSocketResponse | web.WebSocketResponse | None]:
        if uuid_or_name in self.nodes:
            return self.resolve_device_conn(uuid_or_name)
        elif uuid_or_name in self.roles:
            return self.resolve_role_conn(uuid_or_name, prefer_self)
        elif uuid_or_name in self.devices:
            return self.resolve_device_conn(uuid_or_name)
        else:
            raise ValueError(f"No Nodes, Roles, or Devices found with the UUID or name of {uuid_or_name}")

    def ipc_to(
            self,
            requestor: IPCEngine | Role | Device,
            uuid_or_name: str
    ) -> IPCConnection:
        if uuid_or_name in self.nodes:
            conn = self.resolve_node_conn(uuid_or_name)
            dest_node = uuid_or_name
            dest_type = IPCClassType.ENGINE
            dest_name = uuid_or_name
        elif uuid_or_name in self.roles:
            dest_node, conn = self.resolve_role_conn(uuid_or_name)
            dest_type = IPCClassType.ROLE
            dest_name = uuid_or_name
        elif uuid_or_name in self.devices:
            dest_node, conn = self.resolve_device_conn(uuid_or_name)
            dest_type = IPCClassType.DEVICE
            dest_name = uuid_or_name
        else:
            raise ValueError(f"No Nodes, Roles, or Devices found with the UUID or name of {uuid_or_name}")

        if isinstance(requestor, IPCEngine):
            origin_type = IPCClassType.ENGINE
            origin_name = requestor.uuid
            origin_role = None
        elif isinstance(requestor, Role):
            origin_type = IPCClassType.ROLE
            origin_name = requestor.name
            origin_role = None
        elif isinstance(requestor, Device):
            origin_type = IPCClassType.DEVICE
            origin_name = requestor.uuid
            origin_role = requestor.role.name
        else:
            raise ValueError("Requester type %s is not supported.", type(requestor))

        ret = IPCConnection(
            engine=self._engine,
            conn=conn,
            origin_type=origin_type,
            origin_name=origin_name,
            origin_role=origin_role,
            dest_node=dest_node,
            dest_type=dest_type,
            dest_name=dest_name
        )

        return ret


class IPCEngine:

    class Events(Enum):
        # Base Engine Events
        ENGINE_READY = "ENGINE_READY"
        ENGINE_CLOSING = "ENGINE_CLOSING"
        # Node addition/removal.
        WS_NODE_ADDED = "WS_NODE_ADDED"
        NODE_ADDED = "NODE_ADDED"
        WS_NODE_REMOVED = "WS_NODE_REMOVED"
        NODE_REMOVED = "NODE_REMOVED"
        # Role addition/removal.
        WS_ROLE_ADDED = "WS_ROLE_ADDED"
        LOCAL_ROLE_ADDED = "LOCAL_ROLE_ADDED"
        ROLE_ADDED = "ROLE_ADDED"
        WS_ROLE_REMOVED = "WS_ROLE_REMOVED"
        LOCAL_ROLE_REMOVED = "LOCAL_ROLE_REMOVED"
        ROLE_REMOVED = "ROLE_REMOVED"
        # Device addition/removal.
        # Packet handling.
        WS_PACKET = "WS_PACKET"
        LOCAL_PACKET = "LOCAL_PACKET"
        PACKET = "PACKET"
        COMMUNICATION = "COMMUNICATION"

    def __init__(self, uuid_override: str | None = None):
        self.events: DispatchFramework = DispatchFramework()
        self._uuid: str = uuid_override or uuid.uuid1().hex
        # self._connected_nodes: dict[str, web.WebSocketResponse | aiohttp.ClientWebSocketResponse] = {}
        self._roles: dict[str, Role] = {}
        # self._devices: dict[str, Device] = {}
        self.map: ConnectionMap = ConnectionMap(self)
        self.session: aiohttp.ClientSession | None = None

        self.events.add_listener(self.on_engine_ready, self.Events.ENGINE_READY)
        self.events.add_listener(self.on_engine_closing, self.Events.ENGINE_CLOSING)

        self.events.add_listener(self.on_ws_node_added, self.Events.WS_NODE_ADDED)
        self.events.add_listener(self.on_node_added, self.Events.NODE_ADDED)
        self.events.add_listener(self.on_ws_node_removed, self.Events.WS_NODE_REMOVED)
        self.events.add_listener(self.on_node_removed, self.Events.NODE_REMOVED)

        self.events.add_listener(self.on_ws_role_added, self.Events.WS_ROLE_ADDED)
        self.events.add_listener(self.on_local_role_added, self.Events.LOCAL_ROLE_ADDED)
        self.events.add_listener(self.on_role_added, self.Events.ROLE_ADDED)
        self.events.add_listener(self.on_ws_role_removed, self.Events.WS_ROLE_REMOVED)
        self.events.add_listener(self.on_local_role_removed, self.Events.LOCAL_ROLE_REMOVED)
        self.events.add_listener(self.on_role_removed, self.Events.ROLE_REMOVED)

        self.events.add_listener(self.on_ws_packet, self.Events.WS_PACKET)
        self.events.add_listener(self.on_local_packet, self.Events.LOCAL_PACKET)
        self.events.add_listener(self.on_packet, self.Events.PACKET)
        self.events.add_listener(self.on_communication, self.Events.COMMUNICATION)

    @property
    def uuid(self) -> str:
        return self._uuid

    def discovery_payload(self) -> IPCPacket:
        logger.critical("Generating Discovery Payload.")
        ret = IPCPacket(
            payload_type=IPCPayloadType.DISCOVERY,
            origin_type=IPCClassType.ENGINE,
            origin_name=self.uuid,
            origin_role=None,
            dest_type=IPCClassType.ENGINE,
            dest_name=None,
            data=self.uuid,
        )
        return ret

    async def run_stoppable_task(self, coroutine: Coroutine, timeout=0.1) -> asyncio.Task:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coroutine)
        while True:
            try:
                await self.events.wait_for(self.Events.ENGINE_CLOSING, timeout=timeout)
            except asyncio.TimeoutError:
                pass
            else:
                logger.debug("Engine is closing, canceling the task.")
                task.cancel()
                break

            if task.done():
                logger.debug("Task %s finished, ending the loop.", coroutine.__name__)
                break

        return task

    async def quick_send_to(self, uuid_or_name: str, packet: IPCPacket):
        node_uuid, conn = self.map.resolve_conn(uuid_or_name)
        if conn is None:
            self.events.dispatch(self.Events.LOCAL_PACKET, packet)
        else:
            await conn.send_json(packet.to_dict())

    async def propagate_to_nodes(self, packet: IPCPacket):
        for node_uuid, ws in self.map.remote_nodes.items():
            packet.destination_type = IPCClassType.ENGINE
            packet.destination_name = node_uuid
            await ws.send_json(packet.to_dict())

    async def ipc_ws_connect(self, url: str) -> aiohttp.ClientWebSocketResponse | None:
        ws = None
        logger.debug("Starting attempt(s) to establish WS IPC connection to %s", url)
        while not self.session.closed:
            ws = None
            discovered_node = False
            node_uuid = None
            try:
                ws = await self.session.ws_connect(url=url, heartbeat=5)
                await ws.send_json(self.discovery_payload().to_dict())
                async for message in ws:
                    if isinstance(message, aiohttp.WSMessage):
                        if message.type is aiohttp.WSMsgType.text:
                            packet = IPCPacket.from_dict(message.json())
                            if packet.type == IPCPayloadType.DISCOVERY:
                                node_uuid = packet.data
                                self.map.clean()
                                if discovered_node and node_uuid in self.map.nodes:
                                    logger.warning(
                                        "Outgoing Node %s sent a discovery packet despite already being connected. "
                                        "Ignoring it, but is everything okay?", node_uuid
                                    )
                                if self.map.add_node(ws, node_uuid):
                                    logger.debug("Node %s sent a discovery, dispatching event.", node_uuid)
                                    discovered_node = True
                                    self.events.dispatch(self.Events.WS_NODE_ADDED, ws, node_uuid)
                                else:
                                    logger.debug(
                                        "Node %s is already connected to us. Closing outgoing connection.", node_uuid
                                    )
                                    await ws.close()
                                    return ws
                            elif discovered_node is False:
                                logger.debug("Non-discovery JSON message received before a discovery one, discarding.")
                            else:
                                self.events.dispatch(self.Events.WS_PACKET, ws, packet, node_uuid)
                        else:
                            logger.debug("Unhandled non-text message received, discarding.")

                logger.warning("Outgoing connection closed cleanly, perhaps you should dispatch something someday?")

            except (aiohttp.ClientConnectorError, ConnectionRefusedError) as e:
                logger.warning(
                    "Cannot connect or connection lost to %s, waiting %s seconds before retrying.",
                    url,
                    CONNECT_RETRY_SLEEP
                )
                # TODO: More gracefully handle it so _handle_outgoing_ws is also reset?
                await asyncio.sleep(CONNECT_RETRY_SLEEP)

            except aiohttp.WSServerHandshakeError as e:
                match e.status:
                    case 404:
                        logger.error(
                            "Received a 404: %s when connecting to %s, is that route set up for Websockets?",
                            e.message, url
                        )
                        break
                    case _:
                        logger.error(
                            "Received a %s:%s when connecting to %s, no handler available?",
                            e.status, e.message, url
                        )

            except RuntimeError as e:
                logger.exception(
                    "Encountered runtime error while connected to %s, waiting %s seconds before retrying.",
                    url,
                    CONNECT_RETRY_SLEEP,
                    exc_info=e
                )
                await asyncio.sleep(CONNECT_RETRY_SLEEP)

        return ws

    def ipc_middleware(self, route: str = f"/{ENGINE_IPC_ROUTE}"):
        @web.middleware
        async def engine_ipc_middleware(request: web.Request, handler):
            if request.path == route and request.method == "GET":
                return await self.run_stoppable_task(self._ws_ipc_receive_handler(request), timeout=20)
            else:
                return await handler(request)

        return engine_ipc_middleware

    async def _ws_ipc_receive_handler(self, request: web.Request) -> web.WebSocketResponse:
        logger.debug("Incoming WS IPC connection from %s", request.remote)
        discovered_node = False
        node_uuid = None
        ws = web.WebSocketResponse(heartbeat=5)
        await ws.prepare(request)

        await ws.send_json(self.discovery_payload().to_dict())
        async for message in ws:
            if isinstance(message, aiohttp.WSMessage):
                if message.type is aiohttp.WSMsgType.text:
                    packet = IPCPacket.from_dict(message.json())
                    if packet.type == IPCPayloadType.DISCOVERY:
                        node_uuid = packet.data
                        self.map.clean()
                        if self.map.add_node(ws, node_uuid):
                            if discovered_node and node_uuid in self.map.nodes:
                                logger.warning(
                                    "Incoming Node %s sent a discovery packet despite already being connected. "
                                    "Ignoring it, but is everything okay?", node_uuid
                                )
                            else:
                                logger.debug("Incoming Node %s sent a discovery, dispatching event.", node_uuid)
                                discovered_node = True
                                self.events.dispatch(self.Events.WS_NODE_ADDED, ws, node_uuid)  # TODO: Make ws_ipc_node_added?
                        else:
                            logger.debug(
                                "Incoming Node %s is already connected to us, closing incoming connection.", node_uuid
                            )
                            await ws.close()
                            return ws
                    elif not discovered_node:
                        logger.debug("Non-discovery JSON message was sent before a discovery one, ignoring.")
                    else:
                        # logger.debug("Incoming Node %s sent a message, dispatching.", node_uuid)
                        self.events.dispatch(self.Events.WS_PACKET, ws, packet, node_uuid)

        logger.warning("Incoming connection closed cleanly, perhaps you should dispatch something someday?")

        return ws

    # async def on_ws_ipc_message(
    #         self,
    #         ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    #         node_uuid: str,
    #         packet: IPCPacket,
    # ):
    #     self.events.dispatch("ipc_message", node_uuid, packet)

    # @listen(Events.ENGINE_READY)
    async def on_engine_ready(self):
        logger.debug("IPC Engine is ready.")

    # @listen(Events.ENGINE_CLOSING)
    async def on_engine_closing(self):
        logger.debug("IPC Engine is closing.")

    async def on_ws_node_added(self, ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse, node_uuid: str):
        self.map.add_node(ws, node_uuid)
        self.events.dispatch(self.Events.NODE_ADDED, node_uuid)

    async def on_node_added(self, node_uuid: str):
        logger.info("Node added: %s", node_uuid)

    async def on_ws_node_removed(self, ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse, node_uuid: str):
        self.events.dispatch(self.Events.NODE_REMOVED, node_uuid)

    async def on_node_removed(self, node_uuid: str):
        logger.info("Node removed: %s", node_uuid)

    async def on_ws_role_added(
            self,
            ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
            packet: IPCPacket,
            node_uuid: str
    ):
        pass

    async def on_local_role_added(self, role: Role):
        pass

    async def on_role_added(self, role_name: str, node_uuid: str):
        pass

    async def on_ws_role_removed(
            self,
            ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
            packet: IPCPacket | str,
            node_uuid: str
    ):
        pass

    async def on_local_role_removed(self, role: Role):
        pass

    async def on_role_removed(self, role_name: str, node_uuid: str):
        pass

    async def on_ws_packet(
            self,
            ws: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
            packet: IPCPacket,
            node_uuid: str
    ):
        self.events.dispatch(self.Events.PACKET, packet, node_uuid)

    async def on_local_packet(self, packet: IPCPacket):
        self.events.dispatch(self.Events.PACKET, packet, self.uuid)

    async def on_packet(self, packet: IPCPacket, node_uuid: str):
        match packet.type:
            case IPCPayloadType.COMMUNICATION:
                self.events.dispatch(self.Events.COMMUNICATION, packet, node_uuid)
            case IPCPayloadType.DISCOVERY:
                logger.warning("We aren't supposed to be able to handle discovery here?")
            # case IPCPayloadType.ROLE_ADD:
            #     self.events.dispatch("ipc_role_added", packet, node_uuid)
            # case IPCPayloadType.ROLE_REMOVE:
            #     self.events.dispatch("ipc_role_removed", packet, node_uuid)
            # case IPCPayloadType.DEVICE_ADD:
            #     self.events.dispatch("ipc_device_added", packet, node_uuid)
            # case IPCPayloadType.DEVICE_REMOVE:
            #     self.events.dispatch("ipc_device_removed", packet, node_uuid)
            case _:
                logger.warning(
                    "Unknown WS IPC message type encountered from node %s: %s", node_uuid, packet.type
                )

    async def on_communication(self, packet: IPCPacket, origin_node: str | None):
        logger.debug(
            "Communication from node %s for destination type %s, destination name %s received.",
            origin_node, packet.destination_type.name, packet.destination_name
        )
        # logger.debug(packet.data)

    async def add_role(self, role: Role):
        if role.name in self._roles:
            raise ValueError(f"A role with name {role.name} has already been added.")

        self._roles[role.name] = role
        role.set_engine(self)
        if self.session is not None:
            packet = IPCPacket(
                payload_type=IPCPayloadType.ROLE_ADD,
                origin_type=IPCClassType.ENGINE,
                origin_name=self.uuid,
                origin_role=None,
                dest_type=IPCClassType.ENGINE,
                dest_name=None,
                data=role.name,
            )
            await self.propagate_to_nodes(packet)

    async def close(self, closing_time: float = 1.0):
        self.events.dispatch(self.Events.ENGINE_CLOSING)
        await self.session.close()
        await asyncio.sleep(closing_time)

    async def start(self, *, port: int = 8080, discover_nodes: list[tuple[str, int]] | None = None) -> web.BaseSite:
        self.session = aiohttp.ClientSession()
        app = web.Application(middlewares=[self.ipc_middleware()])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port=port, shutdown_timeout=5.0)
        await site.start()
        logger.info("%s listening on %s.", self.__class__.__name__, site.name)

        self.events.dispatch(self.Events.ENGINE_READY)
        if discover_nodes:
            loop = asyncio.get_running_loop()
            for node_address, node_port in discover_nodes:
                loop.create_task(self.run_stoppable_task(
                    self.ipc_ws_connect(f"http://{node_address}:{node_port}/{ENGINE_IPC_ROUTE}")
                ))

        return site

    def run(
            self,
            *,
            loop: asyncio.AbstractEventLoop | None = None,
            port: int = 8080,
            closing_time: float = 1.0,
            discover_nodes: list[tuple[str, int]] | None = None
    ):
        loop = loop or asyncio.new_event_loop()
        task = loop.create_task(self.start(port=port, discover_nodes=discover_nodes))
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.debug("KeyboardInterrupt encountered, stopping loop.")
            if site := task.result():
                logger.debug("Site was created, attempting to stop it.")
                loop.run_until_complete(site.stop())
            else:
                logger.debug("Cancelling start task.")
                task.cancel()

            loop.run_until_complete(self.close(closing_time))
