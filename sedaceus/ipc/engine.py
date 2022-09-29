from __future__ import annotations

import aiohttp
import asyncio
import pickle
import uuid

from aiohttp import web
from logging import getLogger
from typing import Any, Type

from .core import InboundConnection, OutboundConnection, HTTPOverWSRequest, HTTPOverWSResponse
from .local import LocalOutboundConnection

from ..core import DispatchFramework, listen


# WS_ROLE_PATH = "/ws/role/{}"


# TODO: Make connection pool object that dictates how connections work? Redundancy/Fallback, multiple connections, etc?
# TODO: Make connection object that says if something is offline or online? Method that makes a connection?


logger = getLogger(__name__)


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
        self.events.add_listener(self.on_engine_closing, "engine_closing")

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

    async def on_engine_closing(self):
        logger.debug("IPC Engine is closing.")

    async def close(self, closing_time: float = 1.0):
        self.propagate_event("engine_closing")
        await self.session.close()
        await asyncio.sleep(closing_time)

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

    def run(self, *, loop: asyncio.AbstractEventLoop | None = None, port: int = 8080, closing_time: float = 1.0):
        loop = loop or asyncio.new_event_loop()
        task = loop.create_task(self.start(port=port))
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.debug("KeyboardInterrupt encountered, stopping loop.")
            task.cancel()
            loop.run_until_complete(self.close(closing_time))

            # if self.session:
            #     loop.create_task(self.session.close())
            # self.propagate_event("engine_closing")
            # loop.run_until_complete(asyncio.sleep(0))
            # loop.run_until_complete(asyncio.sleep(closing_time))
