from .adapters import register_builtin_platforms
from .base import OutboundIntent, RoomPlatformAdapter
from .registry import (
    build_outbound_intent,
    canonical_platform_key,
    default_display_name,
    default_member_id,
    default_platform_label,
    delivery_kind,
    member_endpoint,
    member_host_message_match_score,
    platform_adapter,
    register_room_platform,
    room_platform_keys,
)

register_builtin_platforms()

__all__ = [
    "OutboundIntent",
    "RoomPlatformAdapter",
    "build_outbound_intent",
    "canonical_platform_key",
    "default_display_name",
    "default_member_id",
    "default_platform_label",
    "delivery_kind",
    "member_endpoint",
    "member_host_message_match_score",
    "platform_adapter",
    "register_room_platform",
    "room_platform_keys",
]
