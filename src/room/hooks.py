import hashlib
from typing import Any

BRIDGE_ROOM_PLATFORM = "maidbridge_room"
_ACCOUNT_ID_KEYS = ("platform_io_account_id", "account_id", "self_id", "bot_account")
_SCOPE_KEYS = ("platform_io_scope", "route_scope", "adapter_scope", "connection_id")


def hook_continue(
    *,
    message: dict[str, Any] | None = None,
    custom_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"action": "continue"}
    if message is not None:
        result["modified_kwargs"] = {"message": message}
    if custom_result is not None:
        result["custom_result"] = custom_result
    return result


def hook_abort(
    *,
    message: dict[str, Any],
    custom_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action": "abort",
        "modified_kwargs": {"message": message},
        "custom_result": custom_result,
    }


async def mark_room_source_message(
    *,
    runtime: Any,
    message: dict[str, Any],
    recorder: Any,
    dispatch_room_source_message: Any | None = None,
) -> dict[str, Any]:
    """把可读 room 来源消息归一化进原生 MaiBot room 会话。"""
    del recorder
    del dispatch_room_source_message
    match = runtime.find_host_message_member(message)
    if match is None:
        return hook_continue()
    room, member = match
    if not member.can_read:
        return hook_continue()

    origin = _source_origin_metadata(message=message, member=member)
    _mark_source_metadata(message=message, room=room, member=member, origin=origin)

    try:
        ingest_result = runtime.ingest_host_message(message)
    except ValueError as exc:
        ingest_result = {"success": False, "error": str(exc)}
    if not ingest_result.get("success"):
        return hook_continue(
            custom_result={
                "room_id": room.room_id,
                "member_id": member.member_id,
                "fail_open": True,
                "stage": "ingest",
                "ingest": ingest_result,
            }
        )
    _normalize_to_bridge_room_message(message=message, room=room, member=member, origin=origin)
    normalized_platform = _coerce_text(message.get("platform"))
    return hook_continue(
        message=message,
        custom_result={
            "room_id": room.room_id,
            "room_name": room.name,
            "member_id": member.member_id,
            "source_member_id": member.member_id,
            "source_platform": origin["platform"],
            "source_group_id": origin["group_id"],
            "source_group_name": origin["group_name"],
            "ingest": ingest_result,
            "normalized_platform": normalized_platform,
        },
    )


def message_additional_config(message: dict[str, Any]) -> dict[str, Any]:
    message_info = message.get("message_info")
    if not isinstance(message_info, dict):
        return {}
    additional_config = message_info.get("additional_config")
    return additional_config if isinstance(additional_config, dict) else {}


def _mark_source_metadata(
    *,
    message: dict[str, Any],
    room: Any,
    member: Any,
    origin: dict[str, str],
) -> None:
    additional_config = _ensure_additional_config(message)
    additional_config.update(
        {
            "maibot_room_id": room.room_id,
            "maibot_room_member_id": member.member_id,
            "maibot_room_role": "source",
            "maibot_source_platform": origin["platform"],
            "maibot_source_channel_id": origin["channel_id"],
        }
    )


def _normalize_to_bridge_room_message(
    *,
    message: dict[str, Any],
    room: Any,
    member: Any,
    origin: dict[str, str],
) -> None:
    message_info = _ensure_message_info(message)
    message_info["group_info"] = {
        "group_id": room.room_id,
        "group_name": room.name or room.room_id,
    }
    session_platform = _room_session_platform(room, origin["platform"])
    additional_config = _ensure_additional_config(message)
    additional_config.update(
        {
            "maidbridge_room_id": room.room_id,
            "maidbridge_room_name": room.name or room.room_id,
            "maidbridge_room_source_member_id": member.member_id,
            "maidbridge_room_session_platform": session_platform,
            "maidbridge_room_original_platform": origin["platform"],
            "maidbridge_room_original_group_id": origin["group_id"],
            "maidbridge_room_original_group_name": origin["group_name"],
            "maidbridge_room_original_channel_id": origin["channel_id"],
            "maidbridge_room_original_message_id": origin["message_id"],
            "maidbridge_room_original_session_id": origin["session_id"],
        }
    )
    _decorate_room_source_text(message=message, origin=origin)
    message["platform"] = session_platform
    message["session_id"] = _room_session_id(
        platform=session_platform,
        room_id=room.room_id,
        additional_config=additional_config,
    )


def _room_session_platform(room: Any, fallback_platform: str) -> str:
    session_platform = _coerce_text(getattr(room, "session_platform", ""))
    return session_platform or fallback_platform or BRIDGE_ROOM_PLATFORM


def _room_session_id(*, platform: str, room_id: str, additional_config: dict[str, Any]) -> str:
    route_components: list[str] = []
    account_id = _pick_route_component(additional_config, _ACCOUNT_ID_KEYS)
    scope = _pick_route_component(additional_config, _SCOPE_KEYS)
    if account_id:
        route_components.append(f"account:{account_id}")
    if scope:
        route_components.append(f"scope:{scope}")
    return hashlib.md5("_".join([platform, *route_components, room_id]).encode()).hexdigest()


def _pick_route_component(additional_config: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _coerce_text(additional_config.get(key))
        if value:
            return value
    return ""


def _source_origin_metadata(*, message: dict[str, Any], member: Any) -> dict[str, str]:
    group_info = _message_group_info(message)
    return {
        "platform": _coerce_text(message.get("platform")),
        "group_id": _coerce_text(group_info.get("group_id")),
        "group_name": _coerce_text(
            group_info.get("group_name") or group_info.get("name") or group_info.get("display_name")
        ),
        "channel_id": _coerce_text(member.endpoint.get("channel_id")),
        "message_id": _coerce_text(message.get("message_id")),
        "session_id": _coerce_text(message.get("session_id")),
    }


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


def _message_group_info(message: dict[str, Any]) -> dict[str, Any]:
    message_info = _ensure_message_info(message)
    group_info = message_info.get("group_info")
    return group_info if isinstance(group_info, dict) else {}


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _decorate_room_source_text(*, message: dict[str, Any], origin: dict[str, str]) -> None:
    prefix = _room_source_prefix(origin)
    if not prefix:
        return
    text = _message_text(message)
    if not text or text.startswith(prefix):
        return
    decorated_text = f"{prefix}{text}"
    message["processed_plain_text"] = decorated_text
    message["display_message"] = decorated_text
    _decorate_raw_message_text(message, prefix=prefix)


def _room_source_prefix(origin: dict[str, str]) -> str:
    platform = _coerce_text(origin.get("platform"))
    source_name = (
        _coerce_text(origin.get("group_name"))
        or _coerce_text(origin.get("channel_id"))
        or _coerce_text(origin.get("group_id"))
    )
    if not platform or not source_name:
        return ""
    return f"[{platform}@{source_name}]:"


def _decorate_raw_message_text(message: dict[str, Any], *, prefix: str) -> None:
    raw_message = message.get("raw_message")
    if not isinstance(raw_message, list):
        return
    for segment in raw_message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            continue
        data = segment.get("data")
        if isinstance(data, dict):
            text = str(data.get("text") or "")
            if text.startswith(prefix):
                return
            data["text"] = f"{prefix}{text}"
            return
        text = str(data or "")
        if text.startswith(prefix):
            return
        segment["data"] = f"{prefix}{text}"
        return
    raw_message.insert(0, {"type": "text", "data": prefix})


def _message_text(message: dict[str, Any]) -> str:
    for key in ("processed_plain_text", "display_message"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw_message = message.get("raw_message")
    if not isinstance(raw_message, list):
        return ""
    parts: list[str] = []
    for segment in raw_message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            continue
        data = segment.get("data")
        if isinstance(data, dict):
            text = str(data.get("text") or "").strip()
        else:
            text = str(data or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


__all__ = [
    "BRIDGE_ROOM_PLATFORM",
    "hook_abort",
    "hook_continue",
    "mark_room_source_message",
    "message_additional_config",
]
