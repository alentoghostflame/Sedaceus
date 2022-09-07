import aiohttp
import asyncio

from datetime import datetime
from logging import getLogger


USER_AGENT = "DiscordBot (www.doesnt.yet/exist/Sedaceus, 0.1)"

TIMER_ERROR_ADD_ON = 0.1


logger = getLogger(__name__)


class RateLimit:
    limit: int
    remaining: int
    reset: datetime | None
    reset_after: float | None
    bucket: str | None

    # _remaining: int
    _first_update: bool
    _reset_remaining_task: asyncio.Task | None
    _on_reset_event: asyncio.Event
    _deny: bool

    def __init__(self):
        self.limit = 1
        self.remaining = 1
        self.reset = None
        self.reset_after = None
        self.bucket = None

        # self._remaining = self.remaining
        self._first_update = True
        self._reset_remaining_task = None
        self._on_reset_event = asyncio.Event()
        self._on_reset_event.set()
        self._deny = False

    def update(self, response: aiohttp.ClientResponse):
        if response.status == 404:
            self._deny = True

        x_limit = response.headers.get("X-RateLimit-Limit")
        self.limit = 1 if x_limit is None else int(x_limit)

        x_remaining = response.headers.get("X-RateLimit-Remaining")
        if x_remaining is None:
            self.remaining = 1
        elif self._first_update:
            self._first_update = False
            self.remaining = int(x_remaining)
        else:
            self.remaining = int(x_remaining) if int(x_remaining) < self.remaining else self.remaining

        x_reset = response.headers.get("X-RateLimit-Reset")
        if x_reset is not None:
            self.reset = datetime.utcfromtimestamp(float(x_reset))
        # else:
        #     self.reset = None if x_reset is None else datetime.utcfromtimestamp(float(x_reset))

        x_reset_after = response.headers.get("X-RateLimit-Reset-After")
        if x_reset_after is not None:
            x_reset_after = float(x_reset_after) + TIMER_ERROR_ADD_ON
            if self.reset_after is None:
                self.reset_after = x_reset_after
            else:
                self.reset_after = x_reset_after if self.reset_after < x_reset_after else self.reset_after
        # if x_reset_after is None:
        #     self.reset_after = None
        # else:
        #     self.reset_after = float(x_reset_after) if float(x_reset_after) < self.reset_after else self.reset_after

        x_bucket = response.headers.get("X-RateLimit-Bucket")
        self.bucket = x_bucket

        if (not self._reset_remaining_task or self._reset_remaining_task.done()) and self.reset_after:
            loop = asyncio.get_running_loop()
            if self.reset_after:
                logger.debug("Bucket %s on path %s will reset after %s seconds.", self.bucket, response.url, self.reset_after)
                self._reset_remaining_task = loop.create_task(self.reset_remaining(self.reset_after))
            else:
                logger.warning("Bucket %s on path %s has no reset_after, resetting immediately.", self.bucket, response.url)
                self._reset_remaining_task = loop.create_task(self.reset_remaining(0))

        if 0 < self.remaining:
            logger.debug("Bucket %s updated with remaining %s, setting reset event.", self.bucket, self.remaining)
            self._on_reset_event.set()

        logger.debug(
            "Bucket %s updated with limit %s, remaining %s, reset %s, and reset_after %s seconds.",
            self.bucket, self.limit, self.remaining, self.reset, self.reset_after,
        )

    async def reset_remaining(self, time: float):
        await asyncio.sleep(time)
        logger.debug("Bucket %s is resetting and setting the event.")
        self.remaining = self.limit
        self._on_reset_event.set()

    async def __aenter__(self):
        await self.acquire()
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def locked(self) -> bool:
        return self.remaining <= 0

    async def acquire(self):
        if self.remaining <= 0:
            logger.warning("Bucket %s has hit the remaining limit of %s, locking until reset.", self.bucket, self.limit)
            self._on_reset_event.clear()

        while not self._on_reset_event.is_set():
            logger.debug("Bucket %s is not set, waiting for it to be set.", self.bucket)
            await self._on_reset_event.wait()

            if self.remaining <= 0:
                logger.warning("Bucket %s has hit the remaining limit of %s, locking until reset.", self.bucket, self.limit)
                self._on_reset_event.clear()

        if self._deny:
            raise ValueError("This request path 404'd and is now denied.")
        logger.debug("Continuing with request.")
        self.remaining -= 1
        return True

    def release(self):
        pass


class RatelimitHandler:
    buckets: dict[str, RateLimit]  # "BucketName": Ratelimit. RateLimits with no bucket name are not included.
    url_rate_limits: dict[str, RateLimit]  # "/guilds/{guild_id}/channels": RateLimit
    url_deny_list: set[str]  # Paths that result in a 404. These will immediately raise an error if encountered again.
    session: aiohttp.ClientSession | None

    _base_url: str | None
    _forced_headers: dict[str, str]

    def __init__(
            self,
            base_url: str | None = None,
            forced_headers: dict[str, str] = {"User-Agent": USER_AGENT},
    ):
        self.buckets = {}
        self.url_rate_limits = {}
        self.url_deny_list = set()
        self.session = None

        self._base_url = base_url
        self._forced_headers = forced_headers

    async def request(
            self,
            method: str,
            url: str,
            params: dict | None = None,
            json: str | None = None,
            headers: dict | None = None,
    ) -> aiohttp.ClientResponse:
        if not self.session:
            self.session = aiohttp.ClientSession(base_url=self._base_url)

        if url in self.url_deny_list:
            raise ValueError(f"The given URL has already resulted in a 404 and was added to the deny list.")
        if url not in self.url_rate_limits:  # TODO: Put Method + URL in rate limits.
            logger.debug("URL %s doesn't have a RateLimit yet, creating.", url)
            self.url_rate_limits[url] = RateLimit()

        if headers:
            used_headers = self._forced_headers | headers
        else:
            used_headers = self._forced_headers

        async with self.url_rate_limits[url]:
            response = await self.session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=used_headers,
            )
            match response.status:
                case 401:
                    logger.warning("Method %s on URL %s resulted in a 401, maybe get a valid auth token?", method, url)
                case 403:
                    logger.warning("Method %s on URL %s resulted in a 403, perhaps check your permissions?", method, url)
                case 404:
                    logger.warning("Method %s on URL %s resulted in a 404, adding to url_deny_list.", method, url)
                    self.url_deny_list.add(url)
                case 429:  # TODO: Have RateLimits with matching buckets be merged.
                    logger.warning("Method %s on URL %s resulted in a 429, possibly rate limit better?", method, url)

            self.url_rate_limits[url].update(response)

        return response



