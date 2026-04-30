import asyncio
import json
from typing import Any

from ...constants import GATEWAY_NAME
from ..protocol import BridgeEnvelope, BridgeProtocolError, build_ack_envelope, build_nack_envelope
from ..protocol.router import RouteDecision, route_envelope
from .builder import RuntimeBundle


class RuntimeRouter:
    def __init__(self, bundle: RuntimeBundle) -> None:
        self._bundle = bundle
        self._started = False
        self._tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        if self._started:
            return
        self._bundle.transport.on_raw(self._handle_raw)
        await self._bundle.transport.start()
        self._started = True
        self._bundle.ctx.logger.info("MaidBridge runtime router started")

    async def stop(self) -> None:
        if not self._started:
            return
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.difference_update(tasks)
        await self._bundle.transport.stop()
        self._started = False
        self._bundle.ctx.logger.info("MaidBridge runtime router stopped")

    async def _handle_raw(self, raw: str) -> None:
        reply_to = ""
        trace_id = ""
        try:
            self._bundle.ctx.logger.debug(f"MaidBridge raw inbound payload received [bytes={len(raw)}]")
            if len(raw.encode("utf-8")) > self._bundle.max_message_bytes:
                raise BridgeProtocolError("envelope exceeds max message size")
            if self._handle_bridge_reply(raw):
                return
            envelope = BridgeEnvelope.loads(raw, max_bytes=self._bundle.max_message_bytes)
            reply_to = envelope.id
            trace_id = envelope.trace_id
            decision = route_envelope(envelope)
            self._bundle.ctx.logger.debug(
                f"MaidBridge routed inbound envelope [event_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"type={envelope.type}, decision={decision.kind}]"
            )
            await self._handle_decision(envelope, decision)
        except (BridgeProtocolError, Exception) as exc:
            self._bundle.ctx.logger.warning(
                f"MaidBridge inbound payload handling failed [reply_to={reply_to}, trace_id={trace_id}, error={exc}]"
            )
            await self._send_nack(reply_to=reply_to, trace_id=trace_id, error=str(exc))

    def _handle_bridge_reply(self, raw: str) -> bool:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict) or data.get("type") not in {"bridge.ack", "bridge.nack"}:
            return False
        try:
            envelope = BridgeEnvelope.from_dict(data)
        except BridgeProtocolError as exc:
            self._bundle.ctx.logger.warning(f"MaidBridge reply ignored: invalid reply envelope [{exc}]")
            return True
        if self._bundle.state is None:
            self._bundle.ctx.logger.debug("MaidBridge reply ignored: runtime state is not configured")
            return True
        reply_to = envelope.reply_to
        if not reply_to:
            self._bundle.ctx.logger.warning(f"MaidBridge reply ignored: missing reply_to [type={data.get('type')}]")
            return True
        request = self._bundle.state.pending_requests.pop(reply_to, None)
        if request is None or request.future is None or request.future.done():
            self._bundle.ctx.logger.warning(
                f"MaidBridge reply ignored: pending request not found [reply_to={reply_to}, "
                f"trace_id={data.get('trace_id', '')}]"
            )
            return True
        request.future.set_result(
            {
                "type": envelope.type,
                "reply_to": reply_to,
                "trace_id": envelope.trace_id,
                "payload": envelope.payload,
            }
        )
        self._bundle.ctx.logger.info(
            f"MaidBridge pending request completed [request_id={reply_to}, trace_id={envelope.trace_id}, "
            f"type={envelope.type}]"
        )
        self._bundle.ctx.logger.debug(
            f"MaidBridge reply payload summary [reply_to={reply_to}]: {_mapping_summary(envelope.payload)}"
        )
        return True

    async def _handle_decision(self, envelope: BridgeEnvelope, decision: RouteDecision) -> None:
        if decision.kind == "nack":
            self._bundle.ctx.logger.warning(
                f"MaidBridge routing rejected envelope [event_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"error={decision.payload['error']}]"
            )
            await self._send_nack(reply_to=envelope.id, trace_id=envelope.trace_id, error=str(decision.payload["error"]))
            return
        if decision.kind == "gateway_message":
            if not self._bundle.enable_message_gateway:
                self._bundle.ctx.logger.warning(
                    f"MaidBridge gateway message rejected: gateway disabled [event_id={envelope.id}, "
                    f"trace_id={envelope.trace_id}]"
                )
                await self._send_nack(
                    reply_to=envelope.id,
                    trace_id=envelope.trace_id,
                    error="MaidBridge message gateway is disabled",
                )
                return
            accepted = await self._bundle.ctx.gateway.route_message(
                GATEWAY_NAME,
                decision.payload["message"],
                route_metadata=decision.payload["route_metadata"],
                external_message_id=envelope.id,
                dedupe_key=decision.payload["dedupe_key"],
            )
            self._bundle.ctx.logger.info(
                f"MaidBridge gateway message routed [event_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"accepted={bool(accepted)}, dedupe_key={decision.payload['dedupe_key']}]"
            )
            await self._send_ack(
                reply_to=envelope.id,
                trace_id=envelope.trace_id,
                payload={
                    "routed": "gateway_message",
                    "accepted": bool(accepted),
                    "dedupe_key": decision.payload["dedupe_key"],
                },
            )
            return
        if decision.kind == "room_message":
            if self._bundle.room_runtime is None:
                self._bundle.ctx.logger.warning(
                    f"MaidBridge room message skipped: room runtime is not configured [event_id={envelope.id}, "
                    f"trace_id={envelope.trace_id}]"
                )
                await self._send_ack(
                    reply_to=envelope.id,
                    trace_id=envelope.trace_id,
                    payload={"accepted": envelope.type, "ingested": False, "skipped": "room runtime is not configured"},
                )
                return
            envelope_data = envelope.to_dict()
            envelope_data["timestamp_ms"] = envelope.timestamp_ms
            message = self._bundle.room_runtime.ingest_maidbridge_out(envelope_data)
            self._bundle.ctx.logger.info(
                f"MaidBridge room message ingested [event_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"room={message.room_id}]"
            )
            await self._send_ack(
                reply_to=envelope.id,
                trace_id=envelope.trace_id,
                payload={
                    "accepted": envelope.type,
                    "ingested": "room_message",
                    "room_id": message.room_id,
                    "room_message_id": message.origin_message_id,
                },
            )
            return
        if decision.kind == "maid_agent_turn":
            handler = self._bundle.maid_agent_turn_handler
            if handler is None:
                self._bundle.ctx.logger.warning(
                    f"MaidBridge maid agent turn rejected: handler is disabled [event_id={envelope.id}, "
                    f"trace_id={envelope.trace_id}]"
                )
                await self._send_nack(
                    reply_to=envelope.id,
                    trace_id=envelope.trace_id,
                    error="MaidBridge maid agent turn handler is disabled",
                )
                return
            self._bundle.ctx.logger.info(
                f"MaidBridge maid agent turn requested [event_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"turn_id={decision.payload['turn_id']}, request_id={decision.payload['request_id']}]"
            )
            self._bundle.ctx.logger.debug(
                f"MaidBridge maid agent turn payload summary: {_envelope_summary(envelope)}"
            )
            task = asyncio.create_task(self._dispatch_maid_agent_turn(envelope, handler))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            await self._send_ack(
                reply_to=envelope.id,
                trace_id=envelope.trace_id,
                payload={
                    "accepted": "maid_agent_turn",
                    "turn_id": decision.payload["turn_id"],
                    "request_id": decision.payload["request_id"],
                },
            )
            return
        if decision.kind == "hello":
            self._record_hello(envelope, decision)
            self._bundle.ctx.logger.info(
                f"MaidBridge endpoint registered [server={envelope.server_id}, endpoint={envelope.endpoint_id}, "
                f"trace_id={envelope.trace_id}]"
            )
            await self._send_ack(reply_to=envelope.id, trace_id=envelope.trace_id, payload=decision.payload)
            return
        await self._send_ack(reply_to=envelope.id, trace_id=envelope.trace_id, payload=decision.payload)

    async def _dispatch_maid_agent_turn(self, envelope: BridgeEnvelope, handler: Any) -> None:
        try:
            reply = await handler.handle(envelope)
            if isinstance(reply, dict) and reply.get("type") == "bridge.nack":
                payload = reply.get("payload")
                error = payload.get("error") if isinstance(payload, dict) else "maid agent turn result was rejected"
                self._bundle.ctx.logger.warning(
                    f"MaidBridge maid agent turn result rejected [event_id={envelope.id}, "
                    f"trace_id={envelope.trace_id}, error={error}]"
                )
                await self._send_nack(reply_to=envelope.id, trace_id=envelope.trace_id, error=str(error))
                return
            self._bundle.ctx.logger.info(
                f"MaidBridge maid agent turn completed [event_id={envelope.id}, trace_id={envelope.trace_id}]"
            )
            self._bundle.ctx.logger.debug(f"MaidBridge maid agent turn reply summary: {_mapping_summary(reply)}")
        except asyncio.CancelledError:
            self._bundle.ctx.logger.warning(
                f"MaidBridge maid agent turn cancelled [event_id={envelope.id}, trace_id={envelope.trace_id}]"
            )
            raise
        except Exception as exc:
            self._bundle.ctx.logger.warning(
                f"MaidBridge maid agent turn failed [event_id={envelope.id}, trace_id={envelope.trace_id}, error={exc}]"
            )
            await self._send_nack(reply_to=envelope.id, trace_id=envelope.trace_id, error=str(exc))

    def _record_hello(self, envelope: BridgeEnvelope, decision: RouteDecision) -> None:
        if self._bundle.state is None:
            return
        self._bundle.state.register_endpoint(
            server_id=envelope.server_id or envelope.source_endpoint,
            endpoint_id=envelope.endpoint_id or envelope.source_endpoint,
            server_name=str(decision.payload["server_name"]),
            source_endpoint=envelope.source_endpoint,
            target_endpoint=envelope.target_endpoint,
            schema_version=envelope.schema_version,
            features=envelope.features,
            capabilities=envelope.capabilities,
        )

    async def _send_ack(self, *, reply_to: str, trace_id: str, payload: dict[str, Any]) -> None:
        await self._send_reply("bridge.ack", reply_to=reply_to, trace_id=trace_id, payload={"ok": True, **payload})

    async def _send_nack(self, *, reply_to: str, trace_id: str, error: str) -> None:
        await self._send_reply("bridge.nack", reply_to=reply_to, trace_id=trace_id, payload={"ok": False, "error": error})

    async def _send_reply(self, reply_type: str, *, reply_to: str, trace_id: str, payload: dict[str, Any]) -> None:
        envelope = (
            build_ack_envelope(reply_to=reply_to, trace_id=trace_id, payload=payload)
            if reply_type == "bridge.ack"
            else build_nack_envelope(reply_to=reply_to, trace_id=trace_id, error=str(payload.get("error") or "error"))
        )
        raw = envelope.dumps(max_bytes=self._bundle.max_message_bytes)
        try:
            await self._bundle.transport.send(raw)
        except Exception as exc:
            self._bundle.ctx.logger.warning(
                f"MaidBridge reply send failed [type={reply_type}, reply_to={reply_to}, trace_id={trace_id}, error={exc}]"
            )
            raise
        self._bundle.ctx.logger.debug(
            f"MaidBridge reply sent [type={reply_type}, reply_to={reply_to}, trace_id={trace_id}, "
            f"payload={_mapping_summary(payload)}]"
        )


def _envelope_summary(envelope: BridgeEnvelope) -> dict[str, Any]:
    return {
        "type": envelope.type,
        "id": envelope.id,
        "trace_id": envelope.trace_id,
        "request_id": envelope.request_id,
        "server_id": envelope.server_id,
        "endpoint_id": envelope.endpoint_id,
        "maid_uuid": bool(envelope.maid_uuid),
        "maid_entity_id": bool(envelope.maid_entity_id),
        "payload": _mapping_summary(envelope.payload),
    }


def _mapping_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    return {
        "keys": sorted(str(key) for key in value.keys()),
        "bytes": len(json.dumps(value, ensure_ascii=False, default=str)),
    }
