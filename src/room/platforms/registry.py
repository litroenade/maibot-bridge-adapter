from collections.abc import Mapping
from typing import Any

from .base import OutboundIntent, RoomPlatformAdapter
from .common import RoomDeliveryKind


_ADAPTERS: dict[str, RoomPlatformAdapter] = {}


def register_room_platform(adapter: RoomPlatformAdapter, *, replace: bool = False) -> None:
    """注册 room 平台适配器。

    Args:
        adapter: 实现 RoomPlatformAdapter 协议的适配器实例。
        replace: 是否允许覆盖同名平台。
    """
    platform_key = _normalize_platform_key(adapter.key)
    if adapter.delivery not in {"sdk", "bridge"}:
        raise ValueError("room platform delivery must be 'sdk' or 'bridge'")
    if platform_key in _ADAPTERS and not replace:
        raise ValueError(f"room platform already registered: {platform_key}")
    _ADAPTERS[platform_key] = adapter


def canonical_platform_key(platform_key: str) -> str:
    return _normalize_platform_key(platform_key)


def room_platform_keys() -> set[str]:
    return set(_ADAPTERS)


def platform_adapter(platform_key: str) -> RoomPlatformAdapter:
    return _adapter(platform_key)


def delivery_kind(platform_key: str) -> RoomDeliveryKind:
    return _adapter(platform_key).delivery


def member_endpoint(platform_key: str, raw_member: Mapping[str, Any], *, default_server_id: str) -> dict[str, Any]:
    return _adapter(platform_key).parse_endpoint(raw_member, default_server_id)


def default_member_id(platform_key: str, endpoint: Mapping[str, Any]) -> str:
    return _adapter(platform_key).default_member_id(endpoint)


def default_platform_label(platform_key: str) -> str:
    return _adapter(platform_key).label


def default_display_name(platform_key: str, endpoint: Mapping[str, Any]) -> str:
    return _adapter(platform_key).default_display_name(endpoint)


def member_host_message_match_score(
    platform_key: str,
    endpoint: Mapping[str, Any],
    *,
    platform: str,
    route_ids: set[str],
) -> int:
    return _adapter(platform_key).host_match_score(platform, endpoint, route_ids)


def build_outbound_intent(
    platform_key: str,
    *,
    room_id: str,
    endpoint: Mapping[str, Any],
    text: str,
    source_member_id: str,
) -> OutboundIntent:
    """按平台生成发送层可执行的出站意图。

    Args:
        platform_key: room member 配置里的 platform。
        room_id: 来源 room ID。
        endpoint: 目标成员 endpoint。
        text: 要发送的文本。
        source_member_id: 来源成员 ID。

    Returns:
        OutboundIntent: 由具体平台适配器生成的投递计划。
    """
    return _adapter(platform_key).build_outbound_intent(room_id, endpoint, text, source_member_id)


def _adapter(platform_key: str) -> RoomPlatformAdapter:
    canonical = canonical_platform_key(platform_key)
    adapter = _ADAPTERS.get(canonical)
    if adapter is None:
        raise ValueError(f"unsupported room platform: {canonical}")
    return adapter


def _normalize_platform_key(platform_key: str) -> str:
    if not isinstance(platform_key, str) or not platform_key.strip():
        raise ValueError("room platform key must be a non-empty string")
    return platform_key.strip().lower()
