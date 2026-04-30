from ..registry import register_room_platform
from .discord import DiscordPlatformAdapter
from .maid import MaidPlatformAdapter
from .qq import QQPlatformAdapter


BUILTIN_PLATFORM_ADAPTERS = (
    QQPlatformAdapter(),
    DiscordPlatformAdapter(),
    MaidPlatformAdapter(),
)


def register_builtin_platforms() -> None:
    for adapter in BUILTIN_PLATFORM_ADAPTERS:
        register_room_platform(adapter, replace=True)


__all__ = [
    "BUILTIN_PLATFORM_ADAPTERS",
    "DiscordPlatformAdapter",
    "MaidPlatformAdapter",
    "QQPlatformAdapter",
    "register_builtin_platforms",
]
