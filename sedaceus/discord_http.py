import aiohttp
import asyncio

from datetime import datetime
from logging import getLogger


__all__ = (
    "RateLimit",
    "RateLimitHandler",
    "RateLimitMigrating",
)


USER_AGENT = "DiscordBot (https://github.com/alentoghostflame/Sedaceus, 0.1)"

# TIMER_ERROR_ADD_ON = 0.4


logger = getLogger(__name__)


class RateLimitMigrating(BaseException):
    ...


class RateLimit:
    limit: int
    remaining: int
    reset: datetime | None
    reset_after: float
    bucket: str | None

    # _remaining: int
    _time_offset: float
    _first_update: bool
    _reset_remaining_task: asyncio.Task | None
    _on_reset_event: asyncio.Event
    _deny: bool
    _migrating: str | None

    def __init__(self, time_offset: float = 0.3) -> None:
        self.limit = 1
        self.remaining = 1
        self.reset = None
        self.reset_after = 1
        self.bucket = None

        # self._remaining = self.remaining
        self._time_offset = time_offset
        self._first_update = True
        self._reset_remaining_task = None
        self._on_reset_event = asyncio.Event()
        self._on_reset_event.set()
        self._deny = False
        self._migrating = None
        """When this RateLimit is being deprecated and acquiring tasks need to migrate to a different RateLimit, this 
        variable should be set to the different RateLimit/buckets string name.
        """

    def update(self, response: aiohttp.ClientResponse) -> None:
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

        x_reset_after = response.headers.get("X-RateLimit-Reset-After")
        if x_reset_after is not None:
            x_reset_after = float(x_reset_after) + self._time_offset
            if self.reset_after is None:
                self.reset_after = x_reset_after
            else:
                self.reset_after = x_reset_after if self.reset_after < x_reset_after else self.reset_after

        x_bucket = response.headers.get("X-RateLimit-Bucket")
        self.bucket = x_bucket

        if not self._reset_remaining_task or self._reset_remaining_task.done():
            self.start_reset_task()

        if 0 < self.remaining and not self._on_reset_event.is_set():
            logger.debug("Bucket %s updated with remaining %s, setting reset event.", self.bucket, self.remaining)
            self._on_reset_event.set()

        logger.debug(
            "Bucket %s updated with limit %s, remaining %s, reset %s, and reset_after %s seconds.",
            self.bucket, self.limit, self.remaining, self.reset, self.reset_after,
        )

    def start_reset_task(self):
        loop = asyncio.get_running_loop()
        logger.debug("Bucket %s will reset after %s seconds.", self.bucket, self.reset_after)
        self._reset_remaining_task = loop.create_task(self.reset_remaining(self.reset_after))

    async def reset_remaining(self, time: float) -> None:
        await asyncio.sleep(time)
        self.remaining = self.limit
        self._on_reset_event.set()
        logger.debug("Bucket %s is reset.", self.bucket)

    @property
    def migrating(self) -> str | None:
        return self._migrating

    def migrate_to(self, bucket: str) -> None:
        self._migrating = bucket
        self.remaining = self.limit
        self._on_reset_event.set()
        logger.debug("Bucket %s is deprecated and acquiring tasks will migrate to a new bucket.")

    async def __aenter__(self) -> None:
        await self.acquire()
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def locked(self) -> bool:
        return self.remaining <= 0

    async def acquire(self) -> bool:
        if self.remaining <= 0 and self._on_reset_event.is_set():
            logger.info("Bucket %s has hit the remaining limit of %s, locking until reset.", self.bucket, self.limit)
            self._on_reset_event.clear()

        while not self._on_reset_event.is_set():
            logger.debug("Bucket %s is not set, waiting for it to be set.", self.bucket)
            await self._on_reset_event.wait()

            if self.remaining <= 0 and self._on_reset_event.is_set():
                logger.info(
                    "Bucket %s has hit the remaining limit of %s, locking until reset.", self.bucket, self.limit
                )
                self._on_reset_event.clear()

        if self.migrating:
            raise RateLimitMigrating(f"This RateLimit is deprecated, you need to migrate to bucket {self.migrating}")
        elif self._deny:
            raise ValueError("This request path 404'd and is now denied.")

        logger.debug("Continuing with request.")
        self.remaining -= 1
        return True

    def release(self) -> None:
        pass


class GlobalRateLimit(RateLimit):
    async def acquire(self) -> bool:
        ret = await super().acquire()
        if not self._reset_remaining_task or self._reset_remaining_task.done():
            self.start_reset_task()

        return ret

    async def update(self, response: aiohttp.ClientResponse) -> None:
        pass


class RateLimitHandler:
    buckets: dict[str | None, RateLimit]  # "BucketName": Ratelimit. RateLimits with no bucket name are not included.
    # The None RateLimit is for global usage.
    url_rate_limits: dict[tuple[str, str], RateLimit]  # ("POST", "/guilds/{guild_id}/channels"): RateLimit
    url_deny_list: set[tuple[str, str]]  # Paths that result in a 404. These will immediately raise an error
    #  if encountered again.
    session: aiohttp.ClientSession | None

    _base_url: str | None
    _forced_headers: dict[str, str]
    _time_offset: float

    def __init__(
            self,
            *,
            base_url: str | None = None,
            forced_headers: dict[str, str] = {"User-Agent": USER_AGENT},
            max_per_interval: int = 50,
            interval_time: int | float = 1,
            time_offset: float = 0.2,
    ) -> None:
        self.buckets = {}
        self.url_rate_limits = {}
        self.url_deny_list = set()
        self.session = None

        self._base_url = base_url
        self._forced_headers = forced_headers
        self._time_offset = time_offset

        self.setup_global_rate_limit(max_per_interval, interval_time)

    def setup_global_rate_limit(self, max_per_interval: int, interval_time: int | float) -> None:
        rate_limit = GlobalRateLimit()
        rate_limit.limit = max_per_interval
        rate_limit.remaining = max_per_interval
        rate_limit.reset_after = interval_time + self._time_offset
        rate_limit.bucket = "Global"

        self.buckets[None] = rate_limit

    async def request(
            self,
            method: str,
            url: str,
            params: dict | None = None,
            json: dict | None = None,
            headers: dict | None = None,
    ) -> aiohttp.ClientResponse:
        # TODO: Add None bucket RateLimit for handling Global rate limits. 50 per second and 10k errors every 10 mins.
        if not self.session:
            self.session = aiohttp.ClientSession(base_url=self._base_url)

        rate_limit_url = (method, url)

        if rate_limit_url in self.url_deny_list:
            raise ValueError(f"The given URL has already resulted in a 404 and was added to the deny list.")
        if rate_limit_url not in self.url_rate_limits:
            logger.critical("URL %s doesn't have a RateLimit yet, creating.", rate_limit_url)
            self.url_rate_limits[rate_limit_url] = RateLimit()

        rate_limit = self.url_rate_limits[rate_limit_url]

        if headers:
            used_headers = self._forced_headers | headers
        else:
            used_headers = self._forced_headers

        retry_count = 5  # To prevent infinite loops.

        # The loop is to allow migration to a different RateLimit object if needed.
        async with self.buckets[None]:
            while 0 <= retry_count:  # If we hit this loop retry_count times, something is wrong.
                should_retry = False
                try:
                    async with rate_limit:
                        # logger.warning("ALL CURRENT BUCKETS: %s\n%s", self.buckets, self.url_rate_limits)
                        # logger.info("Current bucket: %s", rate_limit)
                        response = await self.session.request(
                            method=method,
                            url=url,
                            params=params,
                            json=json,
                            headers=used_headers,
                        )

                        rate_limit.update(response)
                        if rate_limit.bucket is not None and \
                                rate_limit.bucket in self.buckets and \
                                self.buckets[rate_limit.bucket] is not rate_limit:
                            # If the current RateLimit bucket name exists, but the stored RateLimit is not the current
                            #  RateLimit, finish up and signal that the current bucket should be migrated to the
                            #  stored one.
                            logger.debug(
                                "%s has bucket %s that already exists, migrating other possible tasks to that bucket.",
                                rate_limit_url, rate_limit.bucket
                            )
                            correct_rate_limit = self.buckets[rate_limit.bucket]
                            # Signals to all tasks waiting to acquire to migrate.
                            rate_limit.migrate_to(correct_rate_limit.bucket)
                            self.url_rate_limits[rate_limit_url] = correct_rate_limit
                            # Update the correct RateLimit object with our findings.
                            correct_rate_limit.update(response)
                        elif rate_limit.bucket is not None:
                            self.buckets[rate_limit.bucket] = rate_limit

                        match response.status:
                            case 401:
                                logger.warning(
                                    "URL %s resulted in a 401, maybe get a valid auth token?", rate_limit_url
                                )
                            case 403:
                                logger.warning(
                                    "URL %s resulted in a 403, perhaps check your permissions?", rate_limit_url
                                )
                            case 404:
                                logger.warning(
                                    "URL %s resulted in a 404, adding to url_deny_list.", rate_limit_url
                                )
                                self.url_deny_list.add(rate_limit_url)
                                raise ValueError(
                                    f"The given URL resulted in a 404 and was added to the deny list."
                                )
                            case 429:  # TODO: Have RateLimits with matching buckets be merged.
                                logger.warning(
                                    "URL %s resulted in a 429, possibly rate limit better?", rate_limit_url
                                )
                                should_retry = True

                except RateLimitMigrating:
                    rate_limit = self.buckets.get(rate_limit.migrating)
                    if rate_limit is None:
                        raise TypeError("RateLimit said to migrate, but the RateLimit to migrate to was not found?")

                else:
                    if not should_retry:
                        break

                finally:
                    retry_count -= 1

            if retry_count <= 0:
                logger.error("Retry count for %s %s hit 0 or less, what's going on?", method, url)

        return response



