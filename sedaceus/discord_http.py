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
    def __init__(self, time_offset: float = 0.3) -> None:
        """Used to time gate a large batch of requests to only occur X every Y seconds. Used via ``async with``

        NOT THREAD SAFE.

        Parameters
        ----------
        time_offset: :class:`float`
            Number in seconds to increase all timers by. Used for lag compensation.
        """
        self.limit: int = 1
        """Maximum amount of requests before requests have to wait for the rate limit to reset."""
        self.remaining: int = 1
        """Remaining amount of requests before requests have to wait for the rate limit to reset."""
        self.reset: datetime | None = None
        """Datetime that the bucket roughly will be reset at."""
        self.reset_after: float = 1.0
        """Amount of seconds roughly until the rate limit will be reset."""
        self.bucket: str | None = None
        """Name of the bucket, if it has one."""

        self._time_offset: float = time_offset
        """Number in seconds to increase all timers by. Used for lag compensation."""
        self._first_update: bool = True
        """If the next update to be ran will be the first."""
        self._reset_remaining_task: asyncio.Task | None = None
        """Holds the task object for resetting the remaining count."""
        self._on_reset_event: asyncio.Event = asyncio.Event()
        """Used to indicate when the rate limit is ready to be acquired."""
        self._on_reset_event.set()
        self._deny: bool = False
        """Set to error all acquiring requests with a 404 value error."""
        self._migrating: str | None = None
        """When this RateLimit is being deprecated and acquiring requests need to migrate to a different RateLimit, this 
        variable should be set to the different RateLimit/buckets string name.
        """

    def update(self, response: aiohttp.ClientResponse) -> None:
        """Updates the rate limit with information found in the response. Specifically the headers."""
        if response.status == 404:
            self._deny = True

        # Updates the limit if it exists.
        x_limit = response.headers.get("X-RateLimit-Limit")
        self.limit = 1 if x_limit is None else int(x_limit)

        # Updates the remaining left if it exists, being pessimistic.
        x_remaining = response.headers.get("X-RateLimit-Remaining")
        if x_remaining is None:
            self.remaining = 1
        elif self._first_update:
            self.remaining = int(x_remaining)
        else:
            # If requests come back out of order, it's possible that we could get a wrong amount remaining.
            # It's best to be pessimistic and assume it cannot go back up unless the reset task occurs.
            self.remaining = int(x_remaining) if int(x_remaining) < self.remaining else self.remaining

        # Updates the datetime of the reset.
        x_reset = response.headers.get("X-RateLimit-Reset")
        if x_reset is not None:
            self.reset = datetime.utcfromtimestamp(float(x_reset))

        # Updates the reset-after count, being pessimistic.
        x_reset_after = response.headers.get("X-RateLimit-Reset-After")
        if x_reset_after is not None:
            x_reset_after = float(x_reset_after) + self._time_offset
            if self.reset_after is None:
                self.reset_after = x_reset_after
            else:
                self.reset_after = x_reset_after if self.reset_after < x_reset_after else self.reset_after

        # Updates the bucket name. The bucket name not existing as fine, as ``None`` is desired for that.
        x_bucket = response.headers.get("X-RateLimit-Bucket")
        self.bucket = x_bucket

        if not self._reset_remaining_task or self._reset_remaining_task.done():
            self.start_reset_task()

        # If for whatever reason we have requests remaining but the reset event isn't set, set it.
        if 0 < self.remaining and not self._on_reset_event.is_set():
            logger.debug("Bucket %s updated with remaining %s, setting reset event.", self.bucket, self.remaining)
            self._on_reset_event.set()

        # If this is our first update, indicate that all future updates aren't the first.
        if self._first_update:
            self._first_update = False

        logger.debug(
            "Bucket %s updated with limit %s, remaining %s, reset %s, and reset_after %s seconds.",
            self.bucket, self.limit, self.remaining, self.reset, self.reset_after,
        )

    def start_reset_task(self):
        """Starts the reset task, non-blocking."""
        loop = asyncio.get_running_loop()
        logger.debug("Bucket %s will reset after %s seconds.", self.bucket, self.reset_after)
        self._reset_remaining_task = loop.create_task(self.reset_remaining(self.reset_after))

    async def reset_remaining(self, time: float) -> None:
        """|coro|
        Sleeps for the specified amount of time, then resets the remaining request count to the limit.

        Parameters
        ----------
        time: :class:`float`
            Amount of time to sleep until the request count is reset to the limit. ``time_offset`` is not added to
            this number.
        """
        await asyncio.sleep(time)
        self.remaining = self.limit
        self._on_reset_event.set()
        logger.debug("Bucket %s is reset.", self.bucket)

    @property
    def migrating(self) -> str | None:
        """If not ``None``, this indicates what bucket acquiring requests should migrate to."""
        return self._migrating

    def migrate_to(self, bucket: str) -> None:
        """Signals to acquiring requests, both present and future, that they need to migrate to a new bucket."""
        self._migrating = bucket
        self.remaining = self.limit
        self._on_reset_event.set()
        logger.debug("Bucket %s is deprecated and acquiring requests will migrate to a new bucket.", bucket)

    async def __aenter__(self) -> None:
        await self.acquire()
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def locked(self) -> bool:
        return self.remaining <= 0

    async def acquire(self) -> bool:
        # If no more requests can be made but the event is set, clear it.
        if self.remaining <= 0 and self._on_reset_event.is_set():
            logger.info("Bucket %s has hit the remaining limit of %s, locking until reset.", self.bucket, self.limit)
            self._on_reset_event.clear()

        # Waits in a loop for the event to be set, clearing the event as needed and looping.
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
        # Basically a placeholder, could probably be removed ;)
        pass


class GlobalRateLimit(RateLimit):
    """
    Represents the global rate limit, and thus has to have slightly modified behavior.
    """
    async def acquire(self) -> bool:
        ret = await super().acquire()
        # As updates no longer occur, it will start the reset task as soon as the first request has acquired.
        if not self._reset_remaining_task or self._reset_remaining_task.done():
            self.start_reset_task()

        return ret

    async def update(self, response: aiohttp.ClientResponse) -> None:
        # The global rate limit doesn't need to be updated. Perhaps with more testing, it will be required?
        pass


class RateLimitHandler:
    def __init__(
            self,
            *,
            base_url: str | None = None,
            forced_headers: dict[str, str] = {"User-Agent": USER_AGENT},
            max_per_interval: int = 50,
            interval_time: int | float = 1,
            time_offset: float = 0.2,
    ) -> None:
        """Handles requests with rate limits in mind.

        NOT THREAD SAFE.

        Parameters
        ----------
        base_url: :class:`str` | ``None``
            URL to pre-pend all requests with. Should not end in a slash, cannot have a path.
            Defaults to not having any base url.
        forced_headers: :class:`dict`[:class:`str`, :class:`str`]
            Headers that should always be added to requests, even to user-specified headers.
            Defaults to adding the User Agent.
        max_per_interval: :class:`int`
            Maximum amount of global requests per ``interval_time`` seconds before requests will be rate limited.
            Defaults to ``50``
        interval_time: :class:`int` | :class:`float`
            How long in seconds until the global rate limit is reset.
            Defaults to ``1``
        time_offset: :class:`float`
            Number in seconds to increase all rate limit timers by. Used for lag compensation.
            Defaults to ``0.2``

        """
        self.buckets: dict[str | None, RateLimit] = {}
        """{"Bucket Name": :class:`RateLimit`} 
        
        Rate limits with no bucket name are not included. The ``None`` bucket is the global bucket.
        """
        self.url_rate_limits: dict[tuple[str, str], RateLimit] = {}
        """{("METHOD", "Route"): :class:`RateLimit`}
        
        Keeps track of per-URL rate limits. Multiple URLs may share a single :class:`RateLimit` object.
        """
        self.url_deny_list: set[tuple[str, str]] = set()
        """{("METHOD", "Route"), ...}
        
        Paths that result in a 404. These will immediately raise an error if a request is made to them.
        """
        self.session: aiohttp.ClientSession | None = None

        self._base_url: str | None = base_url
        """URL to pre-pend all requests with. Should not end in a slash, cannot have a path."""
        self._forced_headers: dict[str, str] = forced_headers
        """Headers that should always be added to requests, even to user-specified headers."""
        self._time_offset: float = time_offset
        """Number in seconds to increase all rate limit timers by. Used for lag compensation."""

        self.setup_global_rate_limit(max_per_interval, interval_time)

    def setup_global_rate_limit(self, max_per_interval: int, interval_time: int | float) -> None:
        """Sets up the global rate limit for the :class:`RateLimitHandler`

        Parameters
        ----------
        max_per_interval: :class:`int`
            Maximum amount of global requests per ``interval_time`` seconds before requests will be rate limited.
        interval_time: :class:`int` | :class:`float`
            How long in seconds until the global rate limit is reset.
        """
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
        # TODO: Is it worth adding a rate limit for 10k errors every 10 mins?
        if not self.session:
            self.session = aiohttp.ClientSession(base_url=self._base_url)

        rate_limit_url = (method, url)

        if rate_limit_url in self.url_deny_list:
            raise ValueError(f"The given URL has already resulted in a 404 and was added to the deny list.")
        if rate_limit_url not in self.url_rate_limits:
            logger.debug("URL %s doesn't have a RateLimit yet, creating.", rate_limit_url)
            self.url_rate_limits[rate_limit_url] = RateLimit()

        rate_limit = self.url_rate_limits[rate_limit_url]

        if headers:
            used_headers = self._forced_headers | headers
        else:
            used_headers = self._forced_headers

        retry_count = 5  # To prevent infinite loops.

        # The loop is to allow migration to a different RateLimit object if needed.
        async with self.buckets[None]:
            # If we hit this loop retry_count times, something is wrong. Either we keep migrating buckets, or 429s keep
            #  getting hit.
            while 0 <= retry_count:
                should_retry = False
                try:
                    async with rate_limit:
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
                                "%s has bucket %s that already exists, migrating other possible requests to that "
                                "bucket.",
                                rate_limit_url, rate_limit.bucket
                            )
                            correct_rate_limit = self.buckets[rate_limit.bucket]
                            # Signals to all requests waiting to acquire to migrate.
                            rate_limit.migrate_to(correct_rate_limit.bucket)
                            self.url_rate_limits[rate_limit_url] = correct_rate_limit
                            # Update the correct RateLimit object with our findings.
                            correct_rate_limit.update(response)
                        elif rate_limit.bucket is not None:
                            self.buckets[rate_limit.bucket] = rate_limit

                        match response.status:
                            # TODO: Raise proper errors for 4xx errors.
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
                            case 429:
                                logger.warning(
                                    "URL %s resulted in a 429, possibly rate limit better?", rate_limit_url
                                )
                                should_retry = True

                except RateLimitMigrating:
                    rate_limit = self.buckets.get(rate_limit.migrating)
                    if rate_limit is None:
                        raise ValueError("RateLimit said to migrate, but the RateLimit to migrate to was not found?")

                else:
                    if not should_retry:
                        break

                finally:
                    retry_count -= 1

            if retry_count <= 0:
                logger.error("Retry count for %s %s hit 0 or less, what's going on?", method, url)

        return response
