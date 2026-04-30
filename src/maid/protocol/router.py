from dataclasses import dataclass
from typing import Any, Mapping

from . import query_api
from .envelope import BridgeEnvelope
from ..gateway.codec import build_gateway_message


@dataclass(frozen=True)
class RouteDecision:
    kind: str
    payload: dict[str, Any]


_REGISTRY_EVENTS = {
    "maid.ai.registry.tools": ("tools", "tools"),
    "maid.ai.registry.skills": ("skills", "skills"),
    "maid.ai.registry.contexts": ("contexts", "contexts"),
    "maid.ai.registry.tasks": ("tasks", "tasks"),
    "maid.ai.registry.sites": ("sites", "sites"),
    "maid.api.registry.tools": ("tools", "tools"),
    "maid.api.registry.skills": ("skills", "skills"),
    "maid.api.registry.contexts": ("contexts", "contexts"),
    "maid.api.registry.tasks": ("tasks", "tasks"),
    "maid.api.registry.sites": ("sites", "sites"),
}

_AI_CHAIN_EVENTS = frozenset(
    {
        "maid.ai.request.received",
        "maid.ai.prompt.built",
        "maid.ai.llm.client.selected",
        "maid.ai.llm.request",
        "maid.ai.llm.raw_response",
        "maid.ai.tool_calls.proposed",
        "maid.ai.tool_call.decoded",
        "maid.ai.tool_result.added",
        "maid.ai.output.final",
        "maid.ai.output.failure",
        "maid.ai.tts.request",
    }
)

_CONTROL_EVENTS = frozenset(
    {
        "bridge.server.hello",
        "bridge.ack",
        "bridge.nack",
    }
)
_DEFAULT_GATEWAY_MAX_HOPS = 8


def route_envelope(envelope: BridgeEnvelope) -> RouteDecision:
    registry_target = _REGISTRY_EVENTS.get(envelope.type)
    if registry_target is not None:
        kind, payload_key = registry_target
        return _route_registry_snapshot(envelope, kind=kind, payload_key=payload_key)
    if envelope.type == "bridge.server.hello":
        return _route_server_hello(envelope)
    if envelope.type == "maid.message.out":
        direction_error = _java_to_client_error(envelope)
        if direction_error is not None:
            return direction_error
        return RouteDecision(kind="room_message", payload={})
    if envelope.type == "maid.agent.turn.request":
        return _route_maid_agent_turn(envelope)
    if envelope.type in _AI_CHAIN_EVENTS or envelope.type in _CONTROL_EVENTS:
        direction_error = _java_to_client_error(envelope)
        if direction_error is not None:
            return direction_error
        return RouteDecision(
            kind="ack",
            payload={
                "accepted": envelope.type,
                "trace_id": envelope.trace_id,
                "request_id": envelope.request_id,
                "callback_id": envelope.callback_id,
            },
        )
    if envelope.type == "bridge.gateway.message":
        return _route_gateway_message(envelope)
    return RouteDecision(kind="nack", payload={"error": f"unsupported envelope type: {envelope.type}"})


def _route_server_hello(envelope: BridgeEnvelope) -> RouteDecision:
    if envelope.direction != "java_to_client":
        return RouteDecision(kind="nack", payload={"error": "bridge.server.hello direction must be java_to_client"})
    server_name = envelope.payload.get("server_name", "")
    if server_name is not None and not isinstance(server_name, str):
        return RouteDecision(kind="nack", payload={"error": "server_name must be a string"})
    return RouteDecision(
        kind="hello",
        payload={
            "accepted": envelope.type,
            "trace_id": envelope.trace_id,
            "request_id": envelope.request_id,
            "callback_id": envelope.callback_id,
            "server_id": envelope.server_id,
            "endpoint_id": envelope.endpoint_id,
            "server_name": server_name or "",
            "source_endpoint": envelope.source_endpoint,
            "target_endpoint": envelope.target_endpoint,
            "schema_version": envelope.schema_version,
            "features": dict(envelope.features),
            "capabilities": dict(envelope.capabilities),
        },
    )


def _route_registry_snapshot(envelope: BridgeEnvelope, *, kind: str, payload_key: str) -> RouteDecision:
    direction_error = _java_to_client_error(envelope)
    if direction_error is not None:
        return direction_error
    raw_items = envelope.payload.get(payload_key)
    if not isinstance(raw_items, list):
        return RouteDecision(kind="nack", payload={"error": f"{payload_key} must be a list"})
    items = [dict(item) for item in raw_items if isinstance(item, Mapping)]
    if len(items) != len(raw_items):
        return RouteDecision(kind="nack", payload={"error": f"{payload_key} entries must be objects"})
    revision = envelope.payload.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        return RouteDecision(kind="nack", payload={"error": "revision must be a non-negative integer"})
    snapshot_id = envelope.payload.get("snapshot_id", "")
    if snapshot_id is not None and not isinstance(snapshot_id, str):
        return RouteDecision(kind="nack", payload={"error": "snapshot_id must be a string"})
    query_api.register_snapshot(
        kind,
        items,
        trace_id=envelope.trace_id,
        server_id=envelope.server_id,
        endpoint_id=envelope.endpoint_id,
        snapshot_id=snapshot_id or "",
        revision=revision,
        source="maidbridge",
        visibility=envelope.visibility,
    )
    return RouteDecision(kind="ack", payload={"updated": kind, "count": len(items)})


def _java_to_client_error(envelope: BridgeEnvelope) -> RouteDecision | None:
    if envelope.direction == "java_to_client":
        return None
    return RouteDecision(kind="nack", payload={"error": f"{envelope.type} direction must be java_to_client"})


def _route_gateway_message(envelope: BridgeEnvelope) -> RouteDecision:
    if envelope.direction != "java_to_client":
        return RouteDecision(kind="nack", payload={"error": "bridge.gateway.message direction must be java_to_client"})
    loop_error = _gateway_loop_error(envelope.payload, max_hops=_DEFAULT_GATEWAY_MAX_HOPS)
    if loop_error:
        return RouteDecision(kind="nack", payload={"error": loop_error})
    plain_text = envelope.payload.get("plain_text")
    actor = envelope.payload.get("actor")
    room = envelope.payload.get("room")
    if not isinstance(plain_text, str) or not plain_text.strip():
        return RouteDecision(kind="nack", payload={"error": "plain_text must be a non-empty string"})
    if not isinstance(actor, Mapping):
        return RouteDecision(kind="nack", payload={"error": "actor must be an object"})
    if not isinstance(room, Mapping):
        return RouteDecision(kind="nack", payload={"error": "room must be an object"})

    actor_id = _non_empty(actor, "id")
    actor_name = _non_empty(actor, "name")
    room_id = _non_empty(room, "id")
    room_name = _non_empty(room, "name")
    if not all((actor_id, actor_name, room_id, room_name)):
        return RouteDecision(kind="nack", payload={"error": "actor and room must include id and name"})

    message, route_metadata, dedupe_key = build_gateway_message(
        envelope=envelope,
        plain_text=plain_text,
        actor_id=actor_id,
        actor_name=actor_name,
        room_id=room_id,
        room_name=room_name,
    )
    return RouteDecision(
        kind="gateway_message",
        payload={
            "message": message,
            "route_metadata": route_metadata,
            "dedupe_key": dedupe_key,
        },
    )


def _route_maid_agent_turn(envelope: BridgeEnvelope) -> RouteDecision:
    direction_error = _java_to_client_error(envelope)
    if direction_error is not None:
        return direction_error
    message = _message_text(envelope.payload.get("message"))
    if not isinstance(message, str) or not message.strip():
        return RouteDecision(kind="nack", payload={"error": "maid agent turn message must be a non-empty string"})
    turn = envelope.payload.get("turn") if isinstance(envelope.payload.get("turn"), Mapping) else {}
    maid = envelope.payload.get("maid") if isinstance(envelope.payload.get("maid"), Mapping) else {}
    turn_id = _first_non_empty(
        turn.get("id") if isinstance(turn, Mapping) else "",
        envelope.payload.get("turn_id"),
        envelope.request_id,
        envelope.id,
    )
    maid_uuid = _first_non_empty(envelope.maid_uuid, maid.get("uuid") if isinstance(maid, Mapping) else "", envelope.payload.get("maid_uuid"))
    maid_entity_id = _first_non_empty(
        envelope.maid_entity_id,
        maid.get("entity_id") if isinstance(maid, Mapping) else "",
        envelope.payload.get("maid_entity_id"),
    )
    if not turn_id:
        return RouteDecision(kind="nack", payload={"error": "maid agent turn turn_id must be non-empty"})
    if not maid_uuid and not maid_entity_id:
        return RouteDecision(kind="nack", payload={"error": "maid_uuid or maid_entity_id must be non-empty"})
    return RouteDecision(
        kind="maid_agent_turn",
        payload={
            "accepted": envelope.type,
            "request_id": envelope.request_id,
            "turn_id": turn_id,
            "message": message,
            "maid_uuid": maid_uuid,
            "maid_entity_id": maid_entity_id,
        },
    )


def _non_empty(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, Mapping):
        return _first_non_empty(message.get("text"), message.get("chat_text"), message.get("content"))
    return ""


def _gateway_loop_error(data: Mapping[str, Any], *, max_hops: int) -> str:
    origin_platform = str(data.get("origin_platform") or "").strip().casefold()
    if origin_platform == "maidbridge":
        return "gateway loop rejected: origin_platform=maidbridge"
    hop_count = data.get("hop_count", 0)
    if isinstance(hop_count, bool):
        return "hop_count must be an integer"
    if not isinstance(hop_count, int):
        return "hop_count must be an integer"
    if hop_count >= max_hops:
        return f"gateway loop rejected: hop_count {hop_count} exceeds limit {max_hops}"
    return ""
