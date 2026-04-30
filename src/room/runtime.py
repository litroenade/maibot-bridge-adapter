"""跨平台会话 room 的运行时。

room 刻意使用稳定成员端点，而不是平台消息 ID。QQ 和 Discord 来源消息按群、
频道或父频道路由匹配；Maid 成员按配置的虚拟频道匹配。maid_uuid 只作为说话
女仆身份保留在消息上，不作为 room 端点身份。
"""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .model import (
    RoomMember,
    RoomMessage,
    build_room_message,
    normalize_room_member,
    render_room_context,
    select_room_targets,
)
from .platforms import (
    build_outbound_intent as build_platform_outbound_intent,
    canonical_platform_key,
    default_display_name as platform_default_display_name,
    default_member_id as platform_default_member_id,
    default_platform_label as platform_default_platform_label,
    delivery_kind,
    member_endpoint as platform_member_endpoint,
    member_host_message_match_score as platform_match_score,
)


@dataclass(frozen=True)
class RoomDefinition:
    room_id: str
    name: str
    session_platform: str
    members: tuple[RoomMember, ...]


def parse_room_config(config: Any, *, default_server_id: str = "minecraft-local") -> list[RoomDefinition]:
    if config is None:
        return []
    if not isinstance(config, list):
        raise ValueError("rooms must be a list")
    rooms: list[RoomDefinition] = []
    seen_room_ids: set[str] = set()
    for index, raw_room in enumerate(config):
        room = _parse_room(raw_room, index=index, default_server_id=default_server_id)
        if room.room_id in seen_room_ids:
            raise ValueError(f"duplicate room id: {room.room_id}")
        seen_room_ids.add(room.room_id)
        rooms.append(room)
    return rooms


class RoomRuntime:
    """集中管理 room 成员、上下文历史和出站路由计划。"""

    def __init__(self, config: Any, *, default_server_id: str = "minecraft-local") -> None:
        self._rooms = parse_room_config(config, default_server_id=default_server_id)
        self._room_by_id = {room.room_id: room for room in self._rooms}
        self._messages_by_room: dict[str, list[RoomMessage]] = {
            room.room_id: [] for room in self._rooms
        }
        self._next_ingest_sequence = 0

    def room_status(self) -> list[dict[str, Any]]:
        return [
            {
                "room_id": room.room_id,
                "name": room.name,
                "session_platform": room.session_platform,
                "member_count": len(room.members),
                "message_count": len(self._messages_by_room[room.room_id]),
            }
            for room in self._rooms
        ]

    def room_members(self, room_id: str) -> list[dict[str, Any]]:
        room = self._require_room(room_id)
        return [_member_snapshot(member) for member in room.members]

    def messages_for_room(self, room_id: str) -> list[RoomMessage]:
        self._require_room(room_id)
        return list(self._messages_by_room[room_id])

    def find_host_message_member(
        self,
        message: Mapping[str, Any],
    ) -> tuple[RoomDefinition, RoomMember] | None:
        """查找 Host 入站消息对应的 room 成员。

        Host 适配器没有统一路由结构：QQ/NapCat 携带群号，Discord 携带频道上下文。
        运行时把这些形态收敛为 route id，让 hook 判断是否需要把原 MaiBot 流程
        接入 room 处理。
        """
        platform = _coerce_id(message.get("platform")).lower()
        route_ids = _host_message_route_ids(message)
        if not platform or not route_ids:
            return None
        best_match: tuple[int, RoomDefinition, RoomMember] | None = None
        for room in self._rooms:
            for member in room.members:
                score = _member_host_message_match_score(member, platform=platform, route_ids=route_ids)
                if score <= 0:
                    continue
                if best_match is None or score > best_match[0]:
                    best_match = (score, room, member)
        if best_match is None:
            return None
        _, room, member = best_match
        return room, member

    def ingest_host_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        match = self.find_host_message_member(message)
        if match is None:
            raise ValueError("host message does not match any readable room member")
        room, member = match
        if not member.can_read:
            raise ValueError(f"room member is not readable: {member.member_id}")

        message_info = _host_message_info(message)
        user_info = _host_user_info(message_info)
        additional_config = _host_additional_config(message_info)
        host_group_name = _host_group_name(message_info)
        text = (
            _coerce_id(message.get("processed_plain_text"))
            or _coerce_id(message.get("display_message"))
            or _host_raw_text(message.get("raw_message"))
        )
        if not text:
            raise ValueError("host message text is empty")
        message_extras = {
            "host_platform": _coerce_id(message.get("platform")),
            "host_session_id": _coerce_id(message.get("session_id")),
            "host_message_id": _coerce_id(message.get("message_id")),
            "additional_config": dict(additional_config),
        }
        if host_group_name:
            message_extras["host_group_name"] = host_group_name
        message_record = self._append_member_message(
            room,
            member,
            user_id=_require_id(user_info.get("user_id"), "user_id"),
            user_display_name=_require_id(user_info.get("user_nickname"), "user_nickname"),
            text=text,
            timestamp_ms=_host_timestamp_ms(message.get("timestamp")),
            origin_message_id=_require_id(message.get("message_id"), "message_id"),
            group_name=host_group_name or None,
            extras=message_extras,
        )
        return _ingest_snapshot(
            message_record,
            context=render_room_context(self._messages_by_room[room.room_id]),
        )

    def room_ingest(
        self,
        *,
        room_id: str,
        member_id: str,
        user_id: Any,
        user_display_name: Any,
        text: Any,
        timestamp_ms: Any,
        origin_message_id: Any,
        extras: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        room = self._require_room(room_id)
        member = self._require_room_member(room, member_id=member_id)
        if not member.can_read:
            raise ValueError(f"room member is not readable: {member.member_id}")
        message = self._append_member_message(
            room,
            member,
            user_id=user_id,
            user_display_name=user_display_name,
            text=text,
            timestamp_ms=timestamp_ms,
            origin_message_id=origin_message_id,
            extras=extras,
        )
        return _ingest_snapshot(
            message,
            context=render_room_context(self._messages_by_room[room.room_id]),
        )

    def ingest_maidbridge_out(self, envelope: Mapping[str, Any]) -> RoomMessage:
        """将 Java 侧 maid 消息写入 room 时间线。

        Bridge room 成员按 ``channel_id`` 定位。信封里的 ``maid_uuid`` 只表示
        正在说话的女仆，类似 Discord bot 或用户身份，不应要求写入 room 配置。
        """
        event_type = _require_string_from_mapping(envelope, "type")
        if event_type != "maid.message.out":
            raise ValueError("only maid.message.out can be ingested into rooms")
        payload = _require_mapping(envelope, "payload")
        maid = payload.get("maid") if isinstance(payload.get("maid"), Mapping) else {}
        message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
        client_info = payload.get("client_info") if isinstance(payload.get("client_info"), Mapping) else {}
        channel_id = (
            _optional_id(envelope.get("channel_id"), "channel_id")
            or _optional_id(payload.get("channel_id"), "channel_id")
            or _optional_id(maid.get("channel_id") if isinstance(maid, Mapping) else None, "channel_id")
            or _require_string_from_mapping(client_info, "channel_id")
        )
        maid_uuid = (
            _optional_id(envelope.get("maid_uuid"), "maid_uuid")
            or _optional_id(payload.get("maid_uuid"), "maid_uuid")
            or _optional_id(maid.get("uuid") if isinstance(maid, Mapping) else None, "maid.uuid")
            or _require_string_from_mapping(maid, "id")
        )
        room, member = self._find_maidbridge_member(channel_id=channel_id)
        text = (
            _optional_id(payload.get("text"), "text")
            or _optional_id(message.get("text") if isinstance(message, Mapping) else None, "message.text")
            or _require_string_from_mapping(payload, "chat_text")
        )
        maid_name = (
            _optional_id(payload.get("maid_name"), "maid_name")
            or _optional_id(maid.get("name") if isinstance(maid, Mapping) else None, "maid.name")
            or member.display_name
        )
        origin_message_id = (
            _optional_id(payload.get("origin_message_id"), "origin_message_id")
            or _optional_id(envelope.get("id"), "id")
            or f"{channel_id}:{maid_uuid}:{self._next_ingest_sequence}"
        )
        timestamp_ms = _require_positive_int(
            envelope.get("timestamp_ms") or payload.get("timestamp_ms"),
            "timestamp_ms",
        )
        return self._append_member_message(
            room,
            member,
            user_id=maid_uuid,
            user_display_name=maid_name,
            text=text,
            timestamp_ms=timestamp_ms,
            origin_message_id=origin_message_id,
            extras={
                "event_type": event_type,
                "event_id": _optional_id(envelope.get("id"), "id"),
                "maid_uuid": maid_uuid,
            },
        )

    def _append_member_message(
        self,
        room: RoomDefinition,
        member: RoomMember,
        *,
        user_id: Any,
        user_display_name: Any,
        text: Any,
        timestamp_ms: Any,
        origin_message_id: Any,
        group_name: Any = None,
        extras: Mapping[str, Any] | None = None,
    ) -> RoomMessage:
        normalized_origin_message_id = _require_id(origin_message_id, "origin_message_id")
        for existing in self._messages_by_room[room.room_id]:
            if existing.member_id == member.member_id and existing.origin_message_id == normalized_origin_message_id:
                return existing
        message = build_room_message(
            member,
            user_id=user_id,
            user_display_name=user_display_name,
            text=text,
            timestamp_ms=timestamp_ms,
            origin_message_id=normalized_origin_message_id,
            ingest_sequence=self._allocate_ingest_sequence(),
            group_name=group_name,
            extras=extras,
        )
        self._messages_by_room[room.room_id].append(message)
        return message

    def room_send(
        self,
        room_id: str,
        *,
        text: str,
        target_member_ids: Iterable[str] | None,
        source_member_id: str = "",
    ) -> dict[str, Any]:
        room = self._require_room(room_id)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        targets = self._select_targets(room, target_member_ids=target_member_ids, source_member_id=source_member_id)
        return {
            "success": True,
            "room_id": room.room_id,
            "planned_targets": [
                {
                    "member_id": member.member_id,
                    "platform": member.platform,
                    "platform_label": member.platform_label,
                    "endpoint": dict(member.endpoint),
                    **_build_target_payload(room=room, member=member, text=text, source_member_id=source_member_id),
                }
                for member in targets
            ],
        }

    def _select_targets(
        self,
        room: RoomDefinition,
        *,
        target_member_ids: Iterable[str] | None,
        source_member_id: str,
    ) -> list[RoomMember]:
        requested = list(target_member_ids or [])
        if not requested:
            raise ValueError("target_member_ids must be provided")
        if requested == ["*"]:
            return select_room_targets(
                room.members,
                target_member_ids=requested,
                source_member_id=source_member_id,
                allow_broadcast=True,
            )
        return select_room_targets(
            room.members,
            target_member_ids=requested,
            source_member_id=source_member_id,
            include_source=True,
        )

    def _require_room(self, room_id: str) -> RoomDefinition:
        room_id = _require_id(room_id, "room_id")
        room = self._room_by_id.get(room_id)
        if room is None:
            raise ValueError(f"unknown room: {room_id}")
        return room

    def _require_room_member(self, room: RoomDefinition, *, member_id: str) -> RoomMember:
        normalized_member_id = _require_id(member_id, "member_id")
        for member in room.members:
            if member.member_id == normalized_member_id:
                return member
        raise ValueError(f"unknown room member: {normalized_member_id}")

    def _find_maidbridge_member(
        self,
        *,
        channel_id: str,
    ) -> tuple[RoomDefinition, RoomMember]:
        for room in self._rooms:
            for member in room.members:
                if member.platform_key != "maid":
                    continue
                if member.endpoint.get("channel_id") == channel_id:
                    return room, member
        raise ValueError(f"unknown maid room member channel_id: {channel_id}")

    def _allocate_ingest_sequence(self) -> int:
        sequence = self._next_ingest_sequence
        self._next_ingest_sequence += 1
        return sequence


def _parse_room(raw_room: Any, *, index: int, default_server_id: str) -> RoomDefinition:
    if not isinstance(raw_room, Mapping):
        raise ValueError(f"room at index {index} must be an object")
    if raw_room.get("strategy") is not None:
        raise ValueError("room strategy is not supported; room routing always uses llm_decide")
    room_id = _require_id(raw_room.get("id") or raw_room.get("room_id"), "room_id")
    raw_members = raw_room.get("members", [])
    if not isinstance(raw_members, list):
        raise ValueError(f"room members must be a list: {room_id}")
    members = tuple(
        _parse_member(raw_member, room_id=room_id, index=member_index, default_server_id=default_server_id)
        for member_index, raw_member in enumerate(raw_members)
    )
    member_ids = [member.member_id for member in members]
    if len(member_ids) != len(set(member_ids)):
        raise ValueError(f"duplicate room member id in {room_id}")
    session_platform = _optional_id(raw_room.get("session_platform"), "session_platform").lower()
    if session_platform == "maidbridge_room":
        raise ValueError("session_platform must be a native platform, not maidbridge_room")
    return RoomDefinition(
        room_id=room_id,
        name=_optional_id(raw_room.get("name"), "name") or room_id,
        session_platform=session_platform or _default_session_platform(members),
        members=members,
    )


def _parse_member(raw_member: Any, *, room_id: str, index: int, default_server_id: str) -> RoomMember:
    if not isinstance(raw_member, Mapping):
        raise ValueError(f"room member at index {index} must be an object")
    if raw_member.get("kind") is not None:
        raise ValueError("kind is not supported in room member config; use platform")
    platform_key = canonical_platform_key(_require_id(raw_member.get("platform"), "platform").lower())
    endpoint = platform_member_endpoint(platform_key, raw_member, default_server_id=default_server_id)
    platform_label = _optional_id(raw_member.get("platform_label"), "platform_label") or _default_platform_label(
        platform_key
    )
    display_name = _optional_id(raw_member.get("display_name"), "display_name") or _default_display_name(
        platform_key, endpoint
    )
    return normalize_room_member(
        room_id=room_id,
        member_id=_optional_id(raw_member.get("member_id"), "member_id")
        or _default_member_id(platform_key, endpoint),
        platform_key=platform_key,
        platform=platform_key,
        platform_label=platform_label,
        display_name=display_name,
        group_name=_optional_id(raw_member.get("group_name"), "group_name") or display_name,
        endpoint=endpoint,
        can_read=_optional_bool(raw_member.get("can_read"), "can_read", default=True),
        can_write=_optional_bool(raw_member.get("can_write"), "can_write", default=True),
    )


def _build_target_payload(
    *,
    room: RoomDefinition,
    member: RoomMember,
    text: str,
    source_member_id: str,
) -> dict[str, Any]:
    """把 room 目标转换成平台出站意图。

    frame 只作为调试快照保留；发送层只消费 intent，避免继续读取平台私有字段。
    """
    intent = build_platform_outbound_intent(
        member.platform_key,
        room_id=room.room_id,
        endpoint=member.endpoint,
        text=text,
        source_member_id=source_member_id,
    )
    return {
        "frame": intent.frame,
        "intent": intent.to_dict(),
    }


def _default_session_platform(members: tuple[RoomMember, ...]) -> str:
    for member in members:
        if delivery_kind(member.platform_key) != "bridge":
            return member.platform
    raise ValueError("room must contain at least one non-bridge member or set a native session_platform")


def _member_snapshot(member: RoomMember) -> dict[str, Any]:
    return {
        "room_id": member.room_id,
        "member_id": member.member_id,
        "platform": member.platform,
        "platform_key": member.platform_key,
        "platform_label": member.platform_label,
        "display_name": member.display_name,
        "group_name": member.group_name,
        "endpoint": dict(member.endpoint),
        "can_read": member.can_read,
        "can_write": member.can_write,
    }


def _ingest_snapshot(message: RoomMessage, *, context: str) -> dict[str, Any]:
    return {
        "success": True,
        "room_id": message.room_id,
        "member_id": message.member_id,
        "message_id": _room_message_id(message),
        "origin_message_id": message.origin_message_id,
        "ingest_sequence": message.ingest_sequence,
        "context": context,
    }


def _room_message_id(message: RoomMessage) -> str:
    return f"{message.room_id}:{message.member_id}:{message.origin_message_id}"


def _member_host_message_match_score(member: RoomMember, *, platform: str, route_ids: set[str]) -> int:
    """计算 Host 侧消息的成员匹配分；maid 成员从信封进入。"""
    return platform_match_score(member.platform_key, member.endpoint, platform=platform, route_ids=route_ids)


def _host_message_route_ids(message: Mapping[str, Any]) -> set[str]:
    """收集 Host 适配器可能保留的所有路由 ID。

    room 配置只按 Discord 频道 ID 匹配；子区 ID 在这里不算路由 ID。
    如果 Discord 适配器保留了父频道上下文，父频道仍可命中 room 成员。
    """
    message_info = _host_message_info(message)
    additional_config = _host_additional_config(message_info)
    group_info = message_info.get("group_info")
    route_ids = {
        _coerce_id(additional_config.get("platform_io_target_group_id")),
        _coerce_id(additional_config.get("parent_channel_id")),
    }
    if isinstance(group_info, Mapping):
        route_ids.add(_coerce_id(group_info.get("group_id")))
    route_ids.update(_host_raw_message_route_ids(message.get("raw_message")))
    return {route_id for route_id in route_ids if route_id}


def _host_raw_message_route_ids(raw_message: Any) -> set[str]:
    route_ids: set[str] = set()

    def visit(segment: Any) -> None:
        if not isinstance(segment, Mapping):
            return
        segment_type = _coerce_id(segment.get("type"))
        data = segment.get("data")
        if segment_type in {"thread_context", "dict"} and isinstance(data, Mapping):
            for key in ("parent_channel_id", "channel_id"):
                route_ids.add(_coerce_id(data.get(key)))
        if segment_type == "seglist" and isinstance(data, list):
            for item in data:
                visit(item)

    if isinstance(raw_message, list):
        for segment in raw_message:
            visit(segment)
    return {route_id for route_id in route_ids if route_id}


def _host_raw_text(raw_message: Any) -> str:
    parts: list[str] = []
    if not isinstance(raw_message, list):
        return ""
    for segment in raw_message:
        if not isinstance(segment, Mapping):
            continue
        if segment.get("type") == "text":
            text = _coerce_id(segment.get("data"))
            if text:
                parts.append(text)
    return " ".join(parts).strip()


def _host_message_info(message: Mapping[str, Any]) -> Mapping[str, Any]:
    message_info = message.get("message_info")
    return message_info if isinstance(message_info, Mapping) else {}


def _host_user_info(message_info: Mapping[str, Any]) -> Mapping[str, Any]:
    user_info = message_info.get("user_info")
    if not isinstance(user_info, Mapping):
        raise ValueError("host message user_info must be an object")
    return user_info


def _host_additional_config(message_info: Mapping[str, Any]) -> Mapping[str, Any]:
    additional_config = message_info.get("additional_config")
    return additional_config if isinstance(additional_config, Mapping) else {}


def _host_group_name(message_info: Mapping[str, Any]) -> str:
    group_info = message_info.get("group_info")
    if isinstance(group_info, Mapping):
        for key in ("group_name", "name", "display_name"):
            value = _coerce_id(group_info.get(key))
            if value:
                return value
    additional_config = _host_additional_config(message_info)
    for key in (
        "group_name",
        "group_display_name",
        "channel_name",
        "channel_display_name",
        "chat_name",
        "chat_display_name",
        "room_name",
        "platform_io_target_group_name",
        "target_group_name",
        "target_channel_name",
        "parent_channel_name",
    ):
        value = _coerce_id(additional_config.get(key))
        if value:
            return value
    return ""


def _host_timestamp_ms(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("timestamp must be numeric")
    if isinstance(value, int):
        return value if value > 100000000000 else value * 1000
    if isinstance(value, float):
        return int(value * 1000)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value) * 1000)
        except ValueError as exc:
            raise ValueError("timestamp must be numeric") from exc
    raise ValueError("timestamp must be numeric")


def _coerce_id(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _default_member_id(platform_key: str, endpoint: Mapping[str, Any]) -> str:
    return platform_default_member_id(platform_key, endpoint)


def _default_platform_label(platform_key: str) -> str:
    return platform_default_platform_label(platform_key)


def _default_display_name(platform_key: str, endpoint: Mapping[str, Any]) -> str:
    return platform_default_display_name(platform_key, endpoint)


def _require_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _require_string_from_mapping(data: Mapping[str, Any], key: str) -> str:
    return _require_id(data.get(key), key)


def _require_id(value: Any, field_name: str) -> str:
    normalized = _optional_id(value, field_name)
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _optional_id(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a string")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()


def _optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _require_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


__all__ = [
    "RoomDefinition",
    "RoomRuntime",
    "parse_room_config",
]
