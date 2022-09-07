from __future__ import annotations

import asyncio

from aiohttp import web
from json import JSONDecodeError
from logging import getLogger
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from typing import TYPE_CHECKING


from ..core import DispatchFramework


if TYPE_CHECKING:
    from typing import Final
    from discord_typings import InteractionData
    from multidict import CIMultiDictProxy


DEFAULT_ROUTE = "/endpoint/interactions"


logger = getLogger(__name__)


__all__: Final[tuple[str, ...]] = (
    "InteractionEndpoint",
    "verify_discord_signature",
)


def verify_discord_signature(public_key: str, signature: str, timestamp: str, body: str) -> bool:
    verify_key = VerifyKey(bytes.fromhex(public_key))
    verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
    return True


class InteractionEndpoint:
    _public_key: str | None
    _route: str | None
    event_dispatcher: DispatchFramework

    def __init__(self):
        self._public_key = None
        self._route = None
        self.event_dispatcher = DispatchFramework()

    def _verify_from_headers(self, headers: CIMultiDictProxy, body: str) -> bool:
        try:
            signature: str = headers.get("x-signature-ed25519")
            timestamp: str = headers.get("x-signature-timestamp")
            verify_discord_signature(self._public_key, signature, timestamp, body)
            return True
        except BadSignatureError:
            return False

    async def _on_interaction_incoming(self, request: web.Request) -> web.Response | None:
        logger.debug("Incoming interaction endpoint request.")
        try:
            body = (await request.read()).decode("utf-8")
            if self._verify_from_headers(request.headers, body):
                logger.debug("Verified Discord signature headers, continuing.")
                interaction: InteractionData = await request.json()
                if interaction.get("type") == 1:
                    self.event_dispatcher.dispatch("ping", request, interaction)
                else:
                    self.event_dispatcher.dispatch("interaction", request, interaction)

                return None
            else:
                logger.debug("Failed Discord signature headers, returning 401.")
                return web.Response(body="invalid request signature", status=401)

        except JSONDecodeError:
            logger.debug("Invalid json given, ignoring: %s", (await request.read()).decode())
            return web.Response(status=400)

    async def handle_ping(self, request: web.Request, interaction):
        response = web.json_response(data={"type": 1})
        await response.prepare(request)
        await response.write_eof()

    def middleware(self, public_key: str, route: str = DEFAULT_ROUTE):
        self._public_key = public_key
        self._route = route

        @web.middleware
        async def interaction_endpoint_middleware(request: web.Request, handler):
            if request.path == self._route and request.method == "POST":
                logger.debug("Request POSTing on interaction endpoint route, intercepting.")
                return await self._on_interaction_incoming(request)
            else:
                resp = await handler(request)
                return resp

        return interaction_endpoint_middleware

    async def start(self, public_key: str, *, port: int = 8080, route: str = DEFAULT_ROUTE):
        app = web.Application(middlewares=[self.middleware(public_key, route)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        self.event_dispatcher.add_listener(self.handle_ping, "ping")
        await site.start()
        logger.info("%s listening on %s on route %s", self.__class__.__name__, site.name, self._route)

    def run(
            self,
            public_key: str,
            *,
            loop: asyncio.AbstractEventLoop | None = None,
            port: int = 8080,
            route: str = DEFAULT_ROUTE,
    ):
        loop = loop or asyncio.new_event_loop()
        task = loop.create_task(self.start(public_key, port=port, route=route))
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.debug("KeyboardInterrupt encountered, stopping loop.")
            task.cancel()
