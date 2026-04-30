from time import time
from typing import Any

from ...constants import PLATFORM, PROTOCOL
from ..protocol.envelope import BridgeEnvelope

_UNSUPPORTED_SEGMENT_TEXT = {
    "image": "[image omitted: Minecraft text chat cannot display images]",
    "voice": "[voice omitted: Minecraft text chat cannot play voice]",
    "file": "[file omitted: Minecraft text chat cannot receive files]",
    "video": "[video omitted: Minecraft text chat cannot play videos]",
}


def build_gateway_message(
    *,
    envelope: BridgeEnvelope,
    plain_text: str,
    actor_id: str,
    actor_name: str,
    room_id: str | None = None,
    room_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not plain_text:
        raise ValueError("plain_text must be non-empty")
    if not actor_id:
        raise ValueError("actor_id must be non-empty")
    if not actor_name:
        raise ValueError("actor_name must be non-empty")

    additional_config = {
        "protocol": PROTOCOL,
        "bridge_trace_id": envelope.trace_id,
        "maidbridge_request_id": envelope.request_id,
        "maidbridge_callback_id": envelope.callback_id,
        "endpoint_id": envelope.endpoint_id,
        "maidbridge_endpoint_id": envelope.endpoint_id,
        "maidbridge_room_id": room_id or envelope.room_id or envelope.endpoint_id,
        "platform_io_account_id": envelope.server_id,
        "platform_io_scope": envelope.server_id,
        "platform_io_target_group_id": room_id or envelope.room_id or envelope.endpoint_id,
        "origin_platform": PLATFORM,
        "origin_message_id": envelope.id,
        "hop_count": 0,
    }
    message_info: dict[str, Any] = {
        "user_info": {
            "user_id": actor_id,
            "user_nickname": actor_name,
            "user_cardname": actor_name,
        },
        "additional_config": additional_config,
    }
    if room_id:
        message_info["group_info"] = {
            "group_id": room_id,
            "group_name": room_name or room_id,
        }

    message = {
        "message_id": envelope.id,
        "timestamp": int(time()),
        "platform": PLATFORM,
        "message_info": message_info,
        "raw_message": [{"type": "text", "data": plain_text}],
        "is_mentioned": False,
        "is_at": False,
        "is_emoji": False,
        "is_picture": False,
        "is_command": False,
        "is_notify": False,
        "session_id": "",
        "reply_to": "",
        "processed_plain_text": plain_text,
        "display_message": plain_text,
    }
    route_metadata = {
        "platform_io_account_id": envelope.server_id,
        "platform_io_scope": envelope.server_id,
        "server_id": envelope.server_id,
        "endpoint_id": envelope.endpoint_id,
        "maidbridge_endpoint_id": envelope.endpoint_id,
        "maidbridge_room_id": room_id or envelope.room_id or envelope.endpoint_id,
        "platform_io_target_group_id": room_id or envelope.room_id or envelope.endpoint_id,
        "bridge_trace_id": envelope.trace_id,
        "protocol": PROTOCOL,
        "relay_id": envelope.trace_id,
        "origin_platform": PLATFORM,
        "origin_message_id": envelope.id,
        "hop_count": 0,
        "allowed_targets": [],
        "deny_reason": "",
    }
    dedupe_key = f"{PROTOCOL}:{envelope.id}"
    return message, route_metadata, dedupe_key


def encode_minecraft_outbound_text(message: dict[str, Any]) -> str:
    raw_message = message.get("raw_message")
    if isinstance(raw_message, list):
        parts = [_encode_segment(segment) for segment in raw_message]
        text = " ".join(part for part in parts if part)
        if text:
            return text
    return str(message.get("processed_plain_text") or message.get("display_message") or "").strip()


def _encode_segment(segment: Any) -> str:
    if not isinstance(segment, dict):
        return str(segment).strip()
    segment_type = str(segment.get("type") or "").strip().casefold()
    if segment_type == "text":
        return str(segment.get("data") or segment.get("text") or "").strip()
    if segment_type == "reply":
        message_id = str(segment.get("message_id") or segment.get("id") or "").strip()
        return f"[reply:{message_id}]" if message_id else "[reply]"
    if segment_type in {"mention", "at"}:
        display = str(
            segment.get("display")
            or segment.get("name")
            or segment.get("user_nickname")
            or segment.get("user_id")
            or segment.get("target")
            or ""
        ).strip()
        return f"@{display}" if display else "@"
    return _UNSUPPORTED_SEGMENT_TEXT.get(segment_type, str(segment.get("data") or "").strip())
