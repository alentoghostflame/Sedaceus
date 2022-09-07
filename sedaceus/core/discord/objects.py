from dataclasses import dataclass
from datetime import datetime

from .enums import ApplicationCommandType, Locale


__all__ = (
    "ApplicationCommand",
    "Snowflake",
)


preset_dataclass = dataclass(eq=True, frozen=True, match_args=True, kw_only=True)


@preset_dataclass
class Snowflake:
    id: int  # ID is assumed to be a 64-bit number. Changes to the bit length will break all of this.

    @property
    def increment(self) -> int:
        # Uses bits 1-12
        return 4095 & self.id

    @property
    def internal_process_id(self) -> int:
        # Uses bits 13-17
        return ((31 << 12) & self.id) >> 12

    @property
    def internal_worker_id(self) -> int:
        # Uses bits 18-22.
        return ((31 << 17) & self.id) >> 17

    @property
    def timestamp(self) -> datetime:
        # Uses bits 23-64. Divide 1000 because datetime.fromtimestamp wants seconds, Discord gives milliseconds.
        return datetime.utcfromtimestamp(((self.id >> 22) + 1420070400000) / 1000)


@preset_dataclass
class ApplicationCommand:
    id: Snowflake
    type: ApplicationCommandType | None = 1
    application_id: Snowflake
    guild_id: Snowflake | None
    name: str
    name_localizations: dict[Locale, str] | None
    description: str

