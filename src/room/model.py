from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RoomMessage:
    room_id: str
    member_id: str
    member_name: str
    platform: str
    platform_label: str
    group_name: str
    user_id: str
    user_display_name: str
    text: str
    timestamp_ms: int
    origin_message_id: str
    ingest_sequence: int | None = None
    extras: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoomMember:
    """稳定的 room 端点，不代表单个用户账号。

    ``platform_key`` 选择适配器边界，``endpoint`` 保存匹配入站流量和构造出站帧
    所需的最小 ID。内置平台统一用 ``channel_id`` 表示 QQ 群、Discord 频道和
    MaidBridge 虚拟女仆频道。
    """

    room_id: str
    member_id: str
    platform_key: str
    platform: str
    platform_label: str
    display_name: str
    group_name: str
    endpoint: dict[str, Any]
    can_read: bool = True
    can_write: bool = True


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value if value.strip() else ""


def _require_positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _copy_extras(extras: Mapping[str, Any] | None) -> dict[str, Any]:
    if extras is None:
        return {}
    if not isinstance(extras, Mapping):
        raise ValueError("extras must be an object")
    return dict(extras)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def normalize_room_message(
    *,
    room_id: Any,
    member_id: Any,
    member_name: Any,
    platform: Any,
    platform_label: Any,
    group_name: Any = "",
    user_id: Any,
    user_display_name: Any,
    text: Any,
    timestamp_ms: Any,
    origin_message_id: Any,
    ingest_sequence: Any = None,
    extras: Mapping[str, Any] | None = None,
) -> RoomMessage:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    return RoomMessage(
        room_id=_require_string(room_id, "room_id"),
        member_id=_require_string(member_id, "member_id"),
        member_name=_require_string(member_name, "member_name"),
        platform=_require_string(platform, "platform"),
        platform_label=_require_string(platform_label, "platform_label"),
        group_name=_optional_string(group_name, "group_name"),
        user_id=_require_string(user_id, "user_id"),
        user_display_name=_require_string(user_display_name, "user_display_name"),
        text=text,
        timestamp_ms=_require_positive_int(timestamp_ms, "timestamp_ms"),
        origin_message_id=_require_string(origin_message_id, "origin_message_id"),
        ingest_sequence=_optional_non_negative_int(ingest_sequence, "ingest_sequence"),
        extras=_copy_extras(extras),
    )


def normalize_room_member(
    *,
    room_id: Any,
    member_id: Any,
    platform_key: Any,
    platform: Any,
    platform_label: Any,
    display_name: Any,
    group_name: Any = "",
    endpoint: Mapping[str, Any] | None = None,
    can_read: Any = True,
    can_write: Any = True,
) -> RoomMember:
    endpoint_data = _copy_extras(endpoint)
    if not endpoint_data:
        raise ValueError("endpoint must be a non-empty object")
    return RoomMember(
        room_id=_require_string(room_id, "room_id"),
        member_id=_require_string(member_id, "member_id"),
        platform_key=_require_string(platform_key, "platform_key"),
        platform=_require_string(platform, "platform"),
        platform_label=_require_string(platform_label, "platform_label"),
        display_name=_require_string(display_name, "display_name"),
        group_name=_optional_string(group_name, "group_name"),
        endpoint=endpoint_data,
        can_read=_require_bool(can_read, "can_read"),
        can_write=_require_bool(can_write, "can_write"),
    )


def build_room_message(
    member: RoomMember,
    *,
    user_id: Any,
    user_display_name: Any,
    text: Any,
    timestamp_ms: Any,
    origin_message_id: Any,
    ingest_sequence: Any = None,
    group_name: Any = None,
    extras: Mapping[str, Any] | None = None,
) -> RoomMessage:
    if not isinstance(member, RoomMember):
        raise ValueError("member must be a RoomMember")
    merged_extras = {
        "platform_key": member.platform_key,
        "platform_label": member.platform_label,
        "member_display_name": member.display_name,
        **member.endpoint,
        **_copy_extras(extras),
    }
    return normalize_room_message(
        room_id=member.room_id,
        member_id=member.member_id,
        member_name=user_display_name,
        platform=member.platform,
        platform_label=member.platform_label,
        group_name=member.group_name if group_name is None else group_name,
        user_id=user_id,
        user_display_name=user_display_name,
        text=text,
        timestamp_ms=timestamp_ms,
        origin_message_id=origin_message_id,
        ingest_sequence=ingest_sequence,
        extras=merged_extras,
    )


def sort_room_messages(messages: Iterable[RoomMessage]) -> list[RoomMessage]:
    indexed_messages = list(enumerate(messages))

    def sort_key(indexed_message: tuple[int, RoomMessage]) -> tuple[int, int, str, str]:
        input_index, message = indexed_message
        if message.ingest_sequence is None:
            return (message.timestamp_ms, input_index, "", "")
        return (
            message.timestamp_ms,
            message.ingest_sequence,
            message.member_id,
            message.origin_message_id,
        )

    return [message for _, message in sorted(indexed_messages, key=sort_key)]


def assign_ingest_sequence(messages: Iterable[RoomMessage], start: int = 0) -> list[RoomMessage]:
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise ValueError("start must be a non-negative integer")
    next_sequence = start
    assigned: list[RoomMessage] = []
    for message in messages:
        if message.ingest_sequence is None:
            assigned.append(replace(message, ingest_sequence=next_sequence))
            next_sequence += 1
        else:
            assigned.append(message)
    return assigned


def render_room_context(messages: Iterable[RoomMessage], max_messages: int | None = None) -> str:
    if max_messages is not None:
        if not isinstance(max_messages, int) or isinstance(max_messages, bool) or max_messages < 0:
            raise ValueError("max_messages must be a non-negative integer")
    sorted_messages = sort_room_messages(messages)
    if max_messages is not None:
        sorted_messages = sorted_messages[-max_messages:] if max_messages else []
    return "\n".join(
        f"{index}. {_render_message(message)}"
        for index, message in enumerate(sorted_messages, start=1)
    )


def _render_message(message: RoomMessage) -> str:
    context_name = message.group_name or message.platform_label
    return f"[{message.member_name} @ {context_name}] {message.text}{_source_suffix(message)}"


def _source_suffix(message: RoomMessage) -> str:
    parts = [
        _coerce_text(getattr(message, "platform_label", "")),
        _coerce_text(getattr(message, "group_name", "")),
        _source_route_id(getattr(message, "extras", None)),
    ]
    marker = " / ".join(part for part in parts if part)
    return f" （来源：{marker}）" if marker else ""


def _source_route_id(extras: Any) -> str:
    if not isinstance(extras, Mapping):
        return ""
    additional_config = extras.get("additional_config")
    if isinstance(additional_config, Mapping):
        for key in (
            "maibot_source_channel_id",
            "platform_io_target_group_id",
            "parent_channel_id",
            "channel_id",
        ):
            value = _coerce_text(additional_config.get(key))
            if value:
                return value
    for key in ("channel_id", "group_id"):
        value = _coerce_text(extras.get(key))
        if value:
            return value
    return ""


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def select_room_targets(
    members: Iterable[RoomMember],
    *,
    target_member_ids: Iterable[str],
    source_member_id: str = "",
    allow_broadcast: bool = False,
    include_source: bool = False,
) -> list[RoomMember]:
    """解析可写目标，不跨适配器边界猜测。

    room 发送可以指定成员，也可以使用广播哨兵值。广播默认排除来源成员；
    显式目标只有在调用方允许时才可指向来源成员。
    """
    member_by_id = {member.member_id: member for member in members}
    requested = list(target_member_ids)
    if not requested:
        raise ValueError("target_member_ids must be non-empty")
    if "*" in requested:
        if requested != ["*"]:
            raise ValueError("broadcast target '*' must be used alone")
        if not allow_broadcast:
            raise ValueError("broadcast target is not allowed")
        return [
            member
            for member in member_by_id.values()
            if member.can_write and (include_source or member.member_id != source_member_id)
        ]

    selected: list[RoomMember] = []
    seen: set[str] = set()
    for member_id in requested:
        if member_id in seen:
            continue
        member = member_by_id.get(member_id)
        if member is None:
            raise ValueError(f"unknown room member: {member_id}")
        if not member.can_write:
            raise ValueError(f"room member is not writable: {member_id}")
        if member.member_id == source_member_id and not include_source:
            raise ValueError(f"source room member cannot be targeted: {member_id}")
        selected.append(member)
        seen.add(member_id)
    return selected


__all__ = [
    "RoomMember",
    "RoomMessage",
    "assign_ingest_sequence",
    "build_room_message",
    "normalize_room_member",
    "normalize_room_message",
    "render_room_context",
    "select_room_targets",
    "sort_room_messages",
]
