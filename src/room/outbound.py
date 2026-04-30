from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .hooks import BRIDGE_ROOM_PLATFORM, message_additional_config


@dataclass(frozen=True)
class BridgeRoomOutboundRoute:
    room_id: str
    source_member_id: str
    target_member_ids: list[str]
    primary_target: dict[str, Any]
    extra_targets: list[dict[str, Any]]
    plan: dict[str, Any]


def is_bridge_room_outbound(message: Mapping[str, Any]) -> bool:
    additional_config = message_additional_config(dict(message))
    if additional_config.get("maidbridge_room_outbound_routed") is True:
        return False
    if str(message.get("platform") or "").strip() == BRIDGE_ROOM_PLATFORM:
        return True
    return bool(_coerce_text(additional_config.get("maidbridge_room_id")))


def build_bridge_room_outbound_route(
    *,
    runtime: Any,
    message: Mapping[str, Any],
    target_member_ids: Iterable[str],
) -> BridgeRoomOutboundRoute:
    room_id = _bridge_room_id(message)
    source_member_id = _bridge_room_source_member_id(message)
    text = _message_text(message)
    plan = runtime.room_send(
        room_id,
        text=text,
        target_member_ids=list(target_member_ids),
        source_member_id=source_member_id,
    )
    targets = [_enrich_target(target, runtime=runtime, room_id=room_id) for target in plan["planned_targets"]]
    primary_index = _primary_target_index(targets)
    primary_target = targets[primary_index]
    extra_targets = [target for index, target in enumerate(targets) if index != primary_index]
    return BridgeRoomOutboundRoute(
        room_id=room_id,
        source_member_id=source_member_id,
        target_member_ids=[target["member_id"] for target in targets],
        primary_target=primary_target,
        extra_targets=extra_targets,
        plan={**plan, "planned_targets": targets},
    )


def mutate_message_to_primary_target(message: dict[str, Any], route: BridgeRoomOutboundRoute) -> None:
    primary = route.primary_target
    group_id = _target_group_id(primary)
    if not group_id:
        raise ValueError(f"room target is missing group/channel id: {primary.get('member_id')}")
    group_name = str(primary.get("group_name") or primary.get("display_name") or group_id).strip()
    message_info = _ensure_message_info(message)
    message_info["group_info"] = {
        "group_id": group_id,
        "group_name": group_name,
    }
    additional_config = _ensure_additional_config(message)
    additional_config.update(
        {
            "maidbridge_room_outbound_routed": True,
            "maidbridge_room_route_room_id": route.room_id,
            "maidbridge_room_route_source_member_id": route.source_member_id,
            "maidbridge_room_route_primary_member_id": primary["member_id"],
            "maidbridge_room_route_primary_platform": primary["platform"],
            "maidbridge_room_route_primary_group_id": group_id,
            "maidbridge_room_route_target_member_ids": list(route.target_member_ids),
            "maidbridge_room_route_extra_member_ids": [target["member_id"] for target in route.extra_targets],
            "platform_io_target_group_id": group_id,
            "target_group_id": group_id,
        }
    )
    message["platform"] = primary["platform"]


def _enrich_target(target: Mapping[str, Any], *, runtime: Any, room_id: str) -> dict[str, Any]:
    member_by_id = {member["member_id"]: member for member in runtime.room_members(room_id)}
    member = member_by_id.get(target.get("member_id"), {})
    enriched = dict(target)
    for key in ("display_name", "group_name", "platform_key"):
        if key in member:
            enriched[key] = member[key]
    return enriched


def _primary_target_index(targets: list[dict[str, Any]]) -> int:
    if not targets:
        raise ValueError("room decision selected no targets")
    for index, target in enumerate(targets):
        if _target_delivery(target) != "bridge":
            return index
    raise ValueError("room route has no native primary target")


def _target_delivery(target: Mapping[str, Any]) -> str:
    """主目标必须是原生 SDK 平台，MaidBridge 目标只能作为额外投递目标。"""
    intent = target.get("intent")
    if not isinstance(intent, Mapping):
        raise ValueError(f"room target is missing outbound intent: {target.get('member_id')}")
    delivery = _coerce_text(intent.get("delivery"))
    if delivery not in {"sdk", "bridge"}:
        raise ValueError(f"invalid room target delivery: {delivery}")
    return delivery


def _bridge_room_id(message: Mapping[str, Any]) -> str:
    additional_config = message_additional_config(dict(message))
    room_id = _coerce_text(additional_config.get("maidbridge_room_id"))
    if room_id:
        return room_id
    message_info = message.get("message_info")
    if isinstance(message_info, Mapping):
        group_info = message_info.get("group_info")
        if isinstance(group_info, Mapping):
            room_id = _coerce_text(group_info.get("group_id"))
            if room_id:
                return room_id
    raise ValueError("bridge room outbound message is missing room id")


def _bridge_room_source_member_id(message: Mapping[str, Any]) -> str:
    additional_config = message_additional_config(dict(message))
    return _coerce_text(additional_config.get("maidbridge_room_source_member_id"))


def _target_group_id(target: Mapping[str, Any]) -> str:
    endpoint = target.get("endpoint")
    if isinstance(endpoint, Mapping):
        for key in ("channel_id", "group_id"):
            value = _coerce_text(endpoint.get(key))
            if value:
                return value
    frame = target.get("frame")
    if isinstance(frame, Mapping):
        for key in ("channel_id", "group_id"):
            value = _coerce_text(frame.get(key))
            if value:
                return value
    return ""


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("processed_plain_text", "display_message", "plain_text", "text"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("bridge room outbound message text is empty")


def _ensure_message_info(message: dict[str, Any]) -> dict[str, Any]:
    message_info = message.setdefault("message_info", {})
    if not isinstance(message_info, dict):
        message_info = {}
        message["message_info"] = message_info
    return message_info


def _ensure_additional_config(message: dict[str, Any]) -> dict[str, Any]:
    message_info = _ensure_message_info(message)
    additional_config = message_info.setdefault("additional_config", {})
    if not isinstance(additional_config, dict):
        additional_config = {}
        message_info["additional_config"] = additional_config
    return additional_config


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


__all__ = [
    "BridgeRoomOutboundRoute",
    "build_bridge_room_outbound_route",
    "is_bridge_room_outbound",
    "mutate_message_to_primary_target",
]
