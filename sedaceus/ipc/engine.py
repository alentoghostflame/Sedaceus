from __future__ import annotations

import aiohttp
import asyncio
import pickle
import uuid

from aiohttp import web
from logging import getLogger
from types import TracebackType
from typing import Any, Type

from ..core import DispatchFramework, listen


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


class IPCDevice:
    def __init__(self, *, uuid_override: str | None = None):
        self._uuid: str = uuid_override or uuid.uuid1().hex

        self.events: DispatchFramework = DispatchFramework()

        self._engine: IPCEngine | None = None
        self._role: DeviceRole | None = None

    @property
    def uuid(self) -> str:
        return self._uuid

    def assign_engine(self, engine: IPCEngine):
        self._engine = engine

    def assign_role(self, role: DeviceRole):
        self._role = role

    async def on_incoming_ws_connection(self, connection: InboundConnection):
        raise NotImplementedError

    async def on_incoming_ws_data(self, connection: InboundConnection, data: Any):
        raise NotImplementedError

    async def create_ws(self, role_name: str, connect: bool = True) -> OutboundConnection:
        target_role = self._engine._local_roles[role_name]
        target_url = target_role.get_connection_url(self.uuid, self._role)
        if target_url is None:
            conn = LocalOutboundConnection(self._engine, self)
        else:
            raise NotImplementedError

        if connect:
            await conn.connect(target_role)

        return conn


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


class InboundConnection:
    connected: bool
    source_uuid: str
    source_role: DeviceRole

    async def send(self, data) -> None:
        raise NotImplementedError

    async def send_pickled(self, data, force_pickle=False) -> None:
        raise NotImplementedError

    async def receive(self) -> Any:
        raise NotImplementedError

    async def send_httplike(self, data) -> HTTPOverWSRequest:
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

    async def __aenter__(self):
        raise NotImplementedError

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        raise NotImplementedError

    def __aiter__(self):
        raise NotImplementedError

    async def __anext__(self):
        raise NotImplementedError


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

    async def __aenter__(self):
        return await LocalConnection.__aenter__(self)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await LocalConnection.__aexit__(self, exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return LocalConnection.__aiter__(self)

    async def __anext__(self):
        return await LocalConnection.__anext__(self)


class DeviceRole:
    def __init__(self, name: str, discoverable: bool = True):
        self._name: str = name
        self.discoverable: bool = discoverable

        self._engine: IPCEngine | None = None
        self._local_devices: dict[str, IPCDevice] = {}

        self.events: DispatchFramework = DispatchFramework()
        # TODO: Use 307 temp redirects to shuffle connecting local clients to other nodes as needed?
        # TODO: Maybe force Roles to be added to all IPCEngines? Instead of "discovering" their roles, just half-merge
        #  them? Force devs to add roles similar to adding Views in NC?

    @property
    def name(self) -> str:
        return self._name

    def assign_engine(self, engine: IPCEngine):
        self._engine = engine

    def propagate_event(self, event, args: list[Any] = [], kwargs: dict[str, Any] = {}):
        self.events.dispatch(event, *args, **kwargs)
        for device in self._local_devices.values():
            device.events.dispatch(event, *args, **kwargs)

    def add_local_device(self, device: IPCDevice):
        if device.uuid in self._local_devices:
            raise ValueError("Local device with UUID %s already exists.", device.uuid)

        self._local_devices[device.uuid] = device

    def get_connection_url(self, requesting_uuid: str, requesting_role: DeviceRole) -> str | None:
        """If this returns None, that means it's a local connection."""
        raise NotImplementedError

    async def on_local_ws_request(
            self,
            inbound_request: InboundConnection
    ):
        raise NotImplementedError


class SingleDeviceRole(DeviceRole):
    def add_local_device(self, device: IPCDevice):
        if self._local_devices:
            raise ValueError("Only one device at a time is handled.")
        else:
            DeviceRole.add_local_device(self, device)

    def get_connection_url(self, requesting_uuid: str, requesting_role: DeviceRole) -> str | None:
        if self._local_devices:
            return None
        else:
            raise NotImplementedError

    async def on_local_ws_request(
            self,
            inbound_request: InboundConnection
    ):
        loop = asyncio.get_event_loop()
        loop.create_task(list(self._local_devices.values())[0].on_incoming_ws_connection(inbound_request))


class IPCEngine:
    def __init__(self):
        self.events: DispatchFramework = DispatchFramework()

        self._local_roles: dict[str, DeviceRole] = {}
        """{"Role Name": :class:`RoleManager`}"""
        self.session: aiohttp.ClientSession | None = None

        self.events.add_listener(self.on_engine_ready, "engine_ready")

    def propagate_event(self, event, args: list[Any] = [], kwargs: dict[str, Any] = {}):
        self.events.dispatch(event, *args, **kwargs)
        for role in self._local_roles.values():
            role.propagate_event(event, args, kwargs)

    def add_local_role(self, role: DeviceRole):
        if role.name in self._local_roles:
            raise ValueError("Role name %s is already used locally and cannot be added.")

        self._local_roles[role.name] = role

    # def discovery_middleware(self, route: str = "/discover"):
    #     @web.middleware
    #     async def engine_discovery_middleware(request: web.Request, handler):
    #         if request.path.startswith(route) and request.method == "GET":
    #             logger.debug("Request from %s to discover.", request.remote)
    #             return web.json_response(self.discovery_payload)
    #         else:
    #             return await handler(request)
    #
    #     return engine_discovery_middleware

    def assign_engine_to_all(self):
        for role in self._local_roles.values():
            role.assign_engine(self)
            for device in role._local_devices.values():
                device.assign_engine(self)

    async def on_engine_ready(self):
        logger.debug("IPC Engine is ready.")

    async def start(self, *, port: int = 8080):
        self.assign_engine_to_all()
        self.session = aiohttp.ClientSession()
        # app = web.Application(middlewares=[self.discovery_middleware()])
        app = web.Application()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port=port)
        await site.start()
        logger.info("%s listening on %s.", self.__class__.__name__, site.name)
        self.propagate_event("engine_ready")

    def run(self, *, loop: asyncio.AbstractEventLoop | None = None, port: int = 8080):
        loop = loop or asyncio.new_event_loop()
        task = loop.create_task(self.start(port=port))
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.debug("KeyboardInterrupt encountered, stopping loop.")
            task.cancel()
            if self.session:
                loop.create_task(self.session.close())
            loop.run_until_complete(asyncio.sleep(0.1))
