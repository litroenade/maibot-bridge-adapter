import json
from dataclasses import dataclass, field
from time import time
from typing import Any, Iterable, Mapping

from ...constants import (
    CLIENT_TO_JAVA,
    DEFAULT_CLIENT_ENDPOINT_ID,
    DEFAULT_DEADLINE_MS,
    DEFAULT_JAVA_ENDPOINT_ID,
    DEFAULT_MAX_MESSAGE_BYTES,
    JAVA_TO_CLIENT,
    PROTOCOL,
    PROTOCOL_VERSION,
)


class BridgeProtocolError(ValueError):
    """MaidBridge 协议载荷格式错误或存在安全风险时抛出。"""


def _require_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BridgeProtocolError(f"{key} must be a non-empty string")
    return value


def _require_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise BridgeProtocolError(f"{key} must be an object")
    return dict(value)


def _require_list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise BridgeProtocolError(f"{key} must be a list")
    return value


def _optional_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BridgeProtocolError(f"{key} must be a string")
    return value


def _optional_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BridgeProtocolError(f"{key} must be an object")
    return dict(value)


def _string_value(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    return str(value) if value is not None and str(value) else ""


def _require_direction(data: Mapping[str, Any]) -> str:
    direction = _require_string(data, "direction")
    if direction not in {CLIENT_TO_JAVA, JAVA_TO_CLIENT}:
        raise BridgeProtocolError(f"unsupported direction: {direction}")
    return direction


def _payload_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    value = payload.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _payload_server_id(data: Mapping[str, Any]) -> str:
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return _first_non_blank(payload.get("server_id"), payload.get("serverId"))


def _payload_maid_value(data: Mapping[str, Any], key: str) -> str:
    maid = _payload_mapping(data, "maid")
    payload = data.get("payload")
    value = maid.get(key)
    if not value and isinstance(payload, Mapping):
        value = payload.get(f"maid_{key}")
    return _first_non_blank(value)


def _payload_sender_value(data: Mapping[str, Any], key: str) -> str:
    sender = _payload_mapping(data, "sender")
    return _first_non_blank(sender.get(key))


def _payload_room_id(data: Mapping[str, Any]) -> str:
    client_info = _payload_mapping(data, "client_info")
    room = _payload_mapping(data, "room")
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return _first_non_blank(client_info.get("room_id"), room.get("id"), payload.get("room_id"))


def _payload_dimension(data: Mapping[str, Any]) -> str:
    state_snapshot = _payload_mapping(data, "state_snapshot")
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return _first_non_blank(payload.get("dimension"), state_snapshot.get("dimension"))


def _default_plane(event_type: str) -> str:
    if event_type.startswith("maid.message.") or event_type.startswith("bridge.gateway."):
        return "message"
    if event_type.startswith("maid.agent."):
        return "agent"
    if (
        event_type.startswith("maid.ai.")
        or event_type.startswith("maid.api.registry.")
        or event_type.startswith("maidbridge.server.")
    ):
        return "diagnostics"
    return "control"


def _first_non_blank(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


@dataclass(frozen=True)
class BridgeEnvelope:
    """Java、传输层和 Python 处理器共享的线级信封。

    这里只校验结构、大小、身份和关联字段。pending 请求归运行时层管理，
    让序列化保持无副作用，也避免匹配回复或超时时修改信封实例。
    """

    protocol: str
    protocol_revision: int
    plane: str
    type: str
    id: str
    trace_id: str
    deadline_ms: int
    payload: dict[str, Any]
    request_id: str = ""
    reply_to: str = ""
    direction: str = JAVA_TO_CLIENT
    source_endpoint: str = DEFAULT_JAVA_ENDPOINT_ID
    target_endpoint: str = DEFAULT_CLIENT_ENDPOINT_ID
    callback_id: str = ""
    causation_id: str = ""
    parent_id: str = ""
    timestamp_ms: int = field(default_factory=lambda: int(time() * 1000))
    server_id: str = ""
    endpoint_id: str = ""
    maid_uuid: str = ""
    maid_entity_id: str = ""
    player_uuid: str = ""
    owner_uuid: str = ""
    dimension: str = ""
    room_id: str = ""
    visibility: str = "private"
    maid_agent_handling: str = "native"
    schema_version: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "protocol": self.protocol,
            "protocol_revision": self.protocol_revision,
            "plane": self.plane,
            "type": self.type,
            "id": self.id,
            "request_id": self.request_id or self.id,
            "trace_id": self.trace_id,
            "deadline_ms": self.deadline_ms,
            "direction": self.direction,
            "source_endpoint": self.source_endpoint,
            "target_endpoint": self.target_endpoint,
            "payload": self.payload,
        }
        optional_fields = {
            "reply_to": self.reply_to,
            "callback_id": self.callback_id,
            "causation_id": self.causation_id,
            "parent_id": self.parent_id,
        }
        for key, value in optional_fields.items():
            if value not in ("", None):
                data[key] = value
        return data

    def dumps(self, *, max_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > max_bytes:
            raise BridgeProtocolError("envelope exceeds max message size")
        return encoded

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BridgeEnvelope":
        protocol = data.get("protocol")
        if protocol != PROTOCOL:
            raise BridgeProtocolError(f"unsupported protocol: {protocol}")
        protocol_revision = data.get("protocol_revision")
        if protocol_revision != PROTOCOL_VERSION:
            raise BridgeProtocolError(f"unsupported protocol revision: {protocol_revision}")
        deadline_ms = data.get("deadline_ms")
        if not isinstance(deadline_ms, int) or deadline_ms <= 0:
            raise BridgeProtocolError("deadline_ms must be a positive integer")
        timestamp_ms = data.get("timestamp_ms", int(time() * 1000))
        if not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
            raise BridgeProtocolError("timestamp_ms must be a positive integer")
        return cls(
            protocol=protocol,
            protocol_revision=protocol_revision,
            plane=_require_string(data, "plane"),
            type=_require_string(data, "type"),
            id=_require_string(data, "id"),
            request_id=_optional_string(data, "request_id") or _require_string(data, "id"),
            reply_to=_optional_string(data, "reply_to"),
            direction=_require_direction(data),
            source_endpoint=_optional_string(data, "source_endpoint") or DEFAULT_JAVA_ENDPOINT_ID,
            target_endpoint=_optional_string(data, "target_endpoint") or DEFAULT_CLIENT_ENDPOINT_ID,
            callback_id=_optional_string(data, "callback_id"),
            causation_id=_optional_string(data, "causation_id"),
            parent_id=_optional_string(data, "parent_id"),
            timestamp_ms=timestamp_ms,
            trace_id=_optional_string(data, "trace_id") or _require_string(data, "id"),
            deadline_ms=deadline_ms,
            server_id=_optional_string(data, "server_id") or _payload_server_id(data),
            endpoint_id=_optional_string(data, "endpoint_id") or _optional_string(data, "source_endpoint"),
            maid_uuid=_optional_string(data, "maid_uuid") or _payload_maid_value(data, "uuid"),
            maid_entity_id=_optional_string(data, "maid_entity_id") or _payload_maid_value(data, "entity_id"),
            player_uuid=_optional_string(data, "player_uuid") or _payload_sender_value(data, "uuid"),
            owner_uuid=_optional_string(data, "owner_uuid"),
            dimension=_optional_string(data, "dimension") or _payload_dimension(data),
            room_id=_optional_string(data, "room_id") or _payload_room_id(data),
            visibility=_optional_string(data, "visibility") or "private",
            maid_agent_handling=_optional_string(data, "maid_agent_handling") or "native",
            schema_version=_optional_string(data, "schema_version"),
            features=_optional_mapping(data, "features"),
            capabilities=_optional_mapping(data, "capabilities"),
            payload=_require_mapping(data, "payload"),
        )

    @classmethod
    def loads(cls, raw: str | bytes, *, max_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> "BridgeEnvelope":
        raw_bytes = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        if len(raw_bytes) > max_bytes:
            raise BridgeProtocolError("envelope exceeds max message size")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError("envelope is not valid JSON") from exc
        if not isinstance(data, Mapping):
            raise BridgeProtocolError("envelope root must be an object")
        return cls.from_dict(data)


def build_llm_request_envelope(
    *,
    request_id: str,
    callback_id: str = "",
    trace_id: str,
    server_id: str,
    endpoint_id: str,
    maid: Mapping[str, Any],
    player: Mapping[str, Any],
    messages: list[Mapping[str, Any]],
    tools: list[Mapping[str, Any]],
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    capabilities: Mapping[str, Any] | None = None,
) -> BridgeEnvelope:
    """创建进入 pending-reply 边界的请求信封。

    ``request_id`` 同时作为协议关联键和运行时 pending 存储的 id；payload
    只保留面向 LLM 的数据，超时和回调路由继续放在信封层。
    """
    if deadline_ms <= 0:
        raise BridgeProtocolError("deadline_ms must be positive")
    maid_data = dict(maid)
    player_data = dict(player)
    maid_uuid = _string_value(maid_data, "uuid")
    maid_entity_id = _string_value(maid_data, "entity_id")
    player_uuid = _string_value(player_data, "uuid")
    payload = {
        "maid": maid_data,
        "player": player_data,
        "messages": [dict(message) for message in messages],
        "tools": [dict(tool) for tool in tools],
        "capabilities": dict(capabilities or {"tool_calls": True, "stream": False}),
    }
    envelope = BridgeEnvelope(
        protocol=PROTOCOL,
        protocol_revision=PROTOCOL_VERSION,
        plane="diagnostics",
        type="maid.ai.llm.request",
        id=request_id,
        request_id=request_id,
        callback_id=callback_id,
        trace_id=trace_id,
        deadline_ms=deadline_ms,
        server_id=server_id,
        endpoint_id=endpoint_id,
        maid_uuid=maid_uuid,
        maid_entity_id=maid_entity_id,
        player_uuid=player_uuid,
        room_id=endpoint_id,
        capabilities=dict(capabilities or {"tool_calls": True, "stream": False}),
        payload=payload,
    )
    BridgeEnvelope.from_dict(envelope.to_dict())
    _require_mapping(payload, "maid")
    _require_mapping(payload, "player")
    _require_list(payload, "messages")
    _require_list(payload, "tools")
    return envelope


def build_ai_event_envelope(
    *,
    event_type: str,
    event_id: str,
    request_id: str,
    trace_id: str,
    server_id: str,
    endpoint_id: str,
    payload: Mapping[str, Any],
    callback_id: str = "",
    causation_id: str = "",
    parent_id: str = "",
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    maid_uuid: str = "",
    maid_entity_id: str = "",
    player_uuid: str = "",
    owner_uuid: str = "",
    dimension: str = "",
    room_id: str = "",
    visibility: str = "private",
    plane: str = "",
    direction: str = CLIENT_TO_JAVA,
    source_endpoint: str = DEFAULT_CLIENT_ENDPOINT_ID,
    target_endpoint: str = DEFAULT_JAVA_ENDPOINT_ID,
    maid_agent_handling: str = "native",
    features: Mapping[str, Any] | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> BridgeEnvelope:
    """创建事件或回复信封，不默认持有 pending 所有权。

    是否等待响应由调用方决定，避免单向 room 事件和网关投递误注册 pending 请求。
    """
    envelope = BridgeEnvelope(
        protocol=PROTOCOL,
        protocol_revision=PROTOCOL_VERSION,
        plane=plane or _default_plane(event_type),
        type=event_type,
        id=event_id,
        request_id=request_id,
        callback_id=callback_id,
        causation_id=causation_id,
        parent_id=parent_id,
        trace_id=trace_id,
        deadline_ms=deadline_ms,
        server_id=server_id,
        endpoint_id=endpoint_id,
        maid_uuid=maid_uuid,
        maid_entity_id=str(maid_entity_id) if maid_entity_id else "",
        player_uuid=player_uuid,
        owner_uuid=owner_uuid,
        dimension=dimension,
        room_id=room_id or endpoint_id,
        visibility=visibility,
        direction=direction,
        source_endpoint=source_endpoint,
        target_endpoint=target_endpoint,
        maid_agent_handling=maid_agent_handling,
        features=dict(features or {}),
        capabilities=dict(capabilities or {}),
        payload=dict(payload),
    )
    BridgeEnvelope.from_dict(envelope.to_dict())
    return envelope


def build_gateway_outbound_envelope(
    *,
    event_id: str,
    trace_id: str,
    server_id: str,
    endpoint_id: str,
    message: Mapping[str, Any],
    route: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
) -> BridgeEnvelope:
    envelope = BridgeEnvelope(
        protocol=PROTOCOL,
        protocol_revision=PROTOCOL_VERSION,
        plane="message",
        type="bridge.gateway.message",
        id=event_id,
        request_id=event_id,
        trace_id=trace_id,
        deadline_ms=deadline_ms,
        server_id=server_id,
        endpoint_id=endpoint_id,
        room_id=endpoint_id,
        visibility="public",
        direction=CLIENT_TO_JAVA,
        source_endpoint=DEFAULT_CLIENT_ENDPOINT_ID,
        target_endpoint=DEFAULT_JAVA_ENDPOINT_ID,
        payload={
            "message": dict(message),
            "route": dict(route or {}),
            "metadata": dict(metadata or {}),
        },
    )
    BridgeEnvelope.from_dict(envelope.to_dict())
    return envelope


def build_client_hello_envelope(
    *,
    client_id: str,
    roles: Iterable[str],
    subscriptions: Iterable[str],
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    trace_id: str = "",
    source_endpoint: str = DEFAULT_CLIENT_ENDPOINT_ID,
    target_endpoint: str = DEFAULT_JAVA_ENDPOINT_ID,
) -> BridgeEnvelope:
    envelope_id = client_id.strip() or f"client-{int(time() * 1000)}"
    envelope = BridgeEnvelope(
        protocol=PROTOCOL,
        protocol_revision=PROTOCOL_VERSION,
        plane="control",
        type="bridge.client.hello",
        id=envelope_id,
        request_id=envelope_id,
        trace_id=trace_id or envelope_id,
        deadline_ms=deadline_ms,
        direction=CLIENT_TO_JAVA,
        source_endpoint=source_endpoint,
        target_endpoint=target_endpoint,
        payload={
            "client_id": envelope_id,
            "roles": [str(role) for role in roles if str(role).strip()],
            "subscriptions": [str(item) for item in subscriptions if str(item).strip()],
        },
    )
    BridgeEnvelope.from_dict(envelope.to_dict())
    return envelope


def build_ack_envelope(
    *,
    reply_to: str,
    trace_id: str,
    payload: Mapping[str, Any] | None = None,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
) -> BridgeEnvelope:
    return _build_reply_envelope(
        reply_type="bridge.ack",
        reply_to=reply_to,
        trace_id=trace_id,
        payload={"ok": True, **dict(payload or {})},
        deadline_ms=deadline_ms,
    )


def build_nack_envelope(
    *,
    reply_to: str,
    trace_id: str,
    error: str,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
) -> BridgeEnvelope:
    return _build_reply_envelope(
        reply_type="bridge.nack",
        reply_to=reply_to,
        trace_id=trace_id,
        payload={"ok": False, "error": error},
        deadline_ms=deadline_ms,
    )


def _build_reply_envelope(
    *,
    reply_type: str,
    reply_to: str,
    trace_id: str,
    payload: Mapping[str, Any],
    deadline_ms: int,
) -> BridgeEnvelope:
    request_id = _first_non_blank(reply_to, f"reply-{int(time() * 1000)}")
    envelope = BridgeEnvelope(
        protocol=PROTOCOL,
        protocol_revision=PROTOCOL_VERSION,
        plane="control",
        type=reply_type,
        id=f"{reply_type.rsplit('.', 1)[-1]}-{request_id}",
        request_id=request_id,
        reply_to=request_id,
        trace_id=_first_non_blank(trace_id, request_id),
        deadline_ms=deadline_ms,
        direction=CLIENT_TO_JAVA,
        source_endpoint=DEFAULT_CLIENT_ENDPOINT_ID,
        target_endpoint=DEFAULT_JAVA_ENDPOINT_ID,
        payload=dict(payload),
    )
    BridgeEnvelope.from_dict(envelope.to_dict())
    return envelope


def make_text_response(content: str) -> dict[str, str]:
    if not isinstance(content, str):
        raise BridgeProtocolError("content must be a string")
    return {"kind": "text", "content": content}


def make_error_response(code: str, message: str) -> dict[str, str]:
    if not code.strip():
        raise BridgeProtocolError("error code must be non-empty")
    if not message.strip():
        raise BridgeProtocolError("error message must be non-empty")
    return {"kind": "error", "code": code, "message": message}


def validate_llm_response_payload(
    payload: Mapping[str, Any],
    *,
    allowed_tool_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    kind = payload.get("kind")
    if kind == "text":
        content = payload.get("content")
        if not isinstance(content, str):
            raise BridgeProtocolError("text response content must be a string")
        return {"kind": "text", "content": content}
    if kind == "error":
        return make_error_response(_require_string(payload, "code"), _require_string(payload, "message"))
    if kind == "tool_calls":
        allowed = set(allowed_tool_names or ())
        tool_calls = _require_list(payload, "tool_calls")
        normalized = [_validate_tool_call(call, allowed) for call in tool_calls]
        if not normalized:
            raise BridgeProtocolError("tool_calls response must include at least one tool call")
        return {"kind": "tool_calls", "tool_calls": normalized}
    raise BridgeProtocolError(f"unsupported response kind: {kind}")


def _validate_tool_call(call: Any, allowed_tool_names: set[str]) -> dict[str, Any]:
    if not isinstance(call, Mapping):
        raise BridgeProtocolError("tool call must be an object")
    call_id = _require_string(call, "id")
    call_type = call.get("type")
    if call_type != "function":
        raise BridgeProtocolError("tool call type must be function")
    function = _require_mapping(call, "function")
    name = _require_string(function, "name")
    if allowed_tool_names and name not in allowed_tool_names:
        raise BridgeProtocolError(f"tool {name} is not exposed")
    arguments = _require_string(function, "arguments")
    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise BridgeProtocolError("tool call arguments must be valid JSON") from exc
    if not isinstance(parsed_arguments, Mapping):
        raise BridgeProtocolError("tool call arguments must decode to an object")
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }
