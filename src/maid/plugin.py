import asyncio
import contextlib
from typing import Any
from uuid import uuid4

from maibot_sdk import API, MessageGateway

from ...config import MaidBridgePluginSettings
from ..constants import CLIENT_TO_JAVA, DEFAULT_ENDPOINT_ID, GATEWAY_NAME, PLATFORM, PROTOCOL
from .agent_turn import MaidAgentTurnService
from .gateway.codec import encode_minecraft_outbound_text
from .protocol import query_api
from .protocol.envelope import build_ai_event_envelope, build_client_hello_envelope, build_gateway_outbound_envelope
from .runtime.builder import build_runtime_bundle
from .runtime.runtime_router import RuntimeRouter
from .runtime.state import PendingRequest
from .transport import AioHttpWebSocketBridgeTransport, BridgeTransport


class MaidBridgeMaidPlugin:
    @MessageGateway(
        name=GATEWAY_NAME,
        route_type="duplex",
        platform=PLATFORM,
        protocol=PROTOCOL,
        description="MaidBridge Minecraft/TouhouLittleMaid 消息网关",
    )
    async def handle_maidbridge_gateway(
        self,
        message: dict[str, Any],
        route: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        settings = self._settings()
        if not settings.enable_message_gateway:
            self.ctx.logger.warning("MaidBridge outbound rejected: message gateway is disabled")
            return {"success": False, "error": "MaidBridge message gateway is disabled"}
        if not self._state.ready:
            self.ctx.logger.warning("MaidBridge outbound rejected: transport is not connected")
            return {"success": False, "error": "MaidBridge transport is not connected"}
        if self._transport is None:
            self.ctx.logger.warning("MaidBridge outbound rejected: transport send loop is not active")
            return {"success": False, "error": "MaidBridge transport send loop is not active"}
        loop_error = self._gateway_loop_error(
            message=message,
            route=route or {},
            metadata=metadata or {},
            settings=settings,
        )
        if loop_error:
            self.ctx.logger.warning(f"MaidBridge outbound rejected: {loop_error}")
            return {"success": False, "error": loop_error}
        outbound_message = dict(message)
        outbound_message["minecraft_text"] = encode_minecraft_outbound_text(outbound_message)
        envelope = build_gateway_outbound_envelope(
            event_id=f"maibot-outbound-{uuid4()}",
            trace_id=f"trace-{uuid4()}",
            server_id=settings.server_id,
            endpoint_id=settings.server_id,
            message=outbound_message,
            route=self._gateway_route_metadata(route or {}, metadata or {}, settings=settings),
            metadata=self._gateway_route_metadata(metadata or {}, route or {}, settings=settings),
            deadline_ms=settings.request_timeout_ms,
        )
        reply = await self._send_envelope_await_reply(envelope, settings=settings)
        payload = reply["payload"]
        if reply["type"] == "bridge.ack" and payload.get("ok", True):
            self.ctx.logger.info(
                f"MaidBridge outbound acknowledged [event_id={envelope.id}, trace_id={envelope.trace_id}]"
            )
            return {
                "success": True,
                "external_message_id": envelope.id,
                "trace_id": envelope.trace_id,
                "ack": payload,
            }
        self.ctx.logger.warning(
            f"MaidBridge outbound rejected by bridge [event_id={envelope.id}, trace_id={envelope.trace_id}, "
            f"error={payload.get('error') or 'unknown'}]"
        )
        return {
            "success": False,
            "error": str(payload.get("error") or "MaidBridge outbound was rejected"),
            "trace_id": envelope.trace_id,
        }

    @API("status", description="获取 MaidBridge 适配器运行状态", version="1", public=True)
    async def get_status(self) -> dict[str, Any]:
        settings = self._settings()
        room_status = self._room_runtime_instance().room_status()
        return {
            "enabled": bool(settings.enabled),
            "ready": bool(self._state.ready),
            "server_id": self._state.server_id or settings.server_id,
            "connection_id": self._state.connection_id,
            "transport_active": self._transport is not None,
            "router_active": self._router is not None,
            "pending_request_count": len(self._state.pending_requests),
            "endpoint_count": len(self._state.endpoints),
            "websocket_role": self._websocket_role(settings),
            "websocket_url": self._websocket_url(settings),
            "protocol": PROTOCOL,
            "gateway_name": GATEWAY_NAME,
            "platform": PLATFORM,
            "max_message_bytes": settings.max_message_bytes,
            "request_timeout_ms": settings.request_timeout_ms,
            "gateway_max_hops": settings.gateway_max_hops,
            "enable_message_gateway": bool(settings.enable_message_gateway),
            "enable_room_gate": bool(settings.enable_room_gate),
            "configured_room_count": len(settings.rooms),
            "loaded_room_count": len(room_status),
            "room_session_context_count": len(self._room_session_context_by_stream_id),
            "rooms": room_status,
        }

    @API("pending_requests", description="列出等待响应的 MaidBridge 请求信封", version="1", public=True)
    async def pending_requests(self) -> list[dict[str, Any]]:
        return [request.snapshot() for _, request in sorted(self._state.pending_requests.items())]

    @API("maid_query", description="发送 MaidBridge 查询信封并等待 bridge ack/nack", version="1", public=True)
    async def maid_query(
        self,
        event_type: str,
        payload: dict[str, Any],
        server_id: str = "",
        endpoint_id: str = "",
        deadline_ms: int = 0,
    ) -> dict[str, Any]:
        return await self._send_maid_envelope(
            event_type,
            payload,
            server_id=server_id,
            endpoint_id=endpoint_id,
            deadline_ms=deadline_ms,
        )

    @API("maid_call", description="发送 MaidBridge 调用信封并等待 bridge ack/nack", version="1", public=True)
    async def maid_call(
        self,
        event_type: str,
        payload: dict[str, Any],
        server_id: str = "",
        endpoint_id: str = "",
        deadline_ms: int = 0,
    ) -> dict[str, Any]:
        return await self._send_maid_envelope(
            event_type,
            payload,
            server_id=server_id,
            endpoint_id=endpoint_id,
            deadline_ms=deadline_ms,
        )

    @API("registry_snapshot", description="获取 MaidBridge 注册表快照", version="1", public=True)
    async def get_registry_snapshot(
        self,
        kind: str,
        server_id: str = "",
        endpoint_id: str = "",
    ) -> dict[str, Any]:
        return query_api.get_snapshot(
            kind,
            server_id=self._resolve_server_id(server_id),
            endpoint_id=self._resolve_endpoint_id(endpoint_id),
        )

    @API("registry_list", description="列出 MaidBridge 注册表条目", version="1", public=True)
    async def list_registry_items(
        self,
        kind: str,
        server_id: str = "",
        endpoint_id: str = "",
    ) -> list[dict[str, Any]]:
        return query_api.list_items(
            kind,
            server_id=self._resolve_server_id(server_id),
            endpoint_id=self._resolve_endpoint_id(endpoint_id),
        )

    @API("registry_get", description="获取单个 MaidBridge 注册表条目", version="1", public=True)
    async def get_registry_item(
        self,
        kind: str,
        key: str,
        server_id: str = "",
        endpoint_id: str = "",
    ) -> dict[str, Any] | None:
        return query_api.get_item(
            kind,
            key,
            server_id=self._resolve_server_id(server_id),
            endpoint_id=self._resolve_optional_endpoint_id(endpoint_id),
        )

    @API("registry_search", description="搜索 MaidBridge 注册表条目", version="1", public=True)
    async def search_registry_items(
        self,
        kind: str,
        text: str,
        server_id: str = "",
        endpoint_id: str = "",
    ) -> list[dict[str, Any]]:
        return query_api.search_items(
            kind,
            text,
            server_id=self._resolve_server_id(server_id),
            endpoint_id=self._resolve_optional_endpoint_id(endpoint_id),
        )

    @API("endpoints", description="列出 MaidBridge 端点注册信息", version="1", public=True)
    async def list_endpoints(self) -> list[dict[str, Any]]:
        return [
            {
                **endpoint,
                "features": dict(endpoint.get("features", {})),
                "capabilities": dict(endpoint.get("capabilities", {})),
            }
            for _, endpoint in sorted(self._state.endpoints.items())
        ]

    def _resolve_server_id(self, server_id: str) -> str:
        normalized = server_id.strip()
        return normalized or self._settings().server_id

    def _resolve_endpoint_id(self, endpoint_id: str) -> str:
        normalized = endpoint_id.strip()
        return normalized or DEFAULT_ENDPOINT_ID

    def _resolve_optional_endpoint_id(self, endpoint_id: str) -> str | None:
        normalized = endpoint_id.strip()
        return normalized or None

    async def _send_maid_envelope(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        server_id: str,
        endpoint_id: str,
        deadline_ms: int,
    ) -> dict[str, Any]:
        settings = self._settings()
        if not self._state.ready:
            return {"ok": False, "error": "MaidBridge transport is not connected"}
        if self._transport is None:
            return {"ok": False, "error": "MaidBridge transport send loop is not active"}
        payload = self._payload_with_default_maid(event_type, payload, settings)
        request_id = f"maibot-maid-{uuid4()}"
        envelope = build_ai_event_envelope(
            event_type=event_type,
            event_id=request_id,
            request_id=request_id,
            trace_id=f"trace-{uuid4()}",
            server_id=self._resolve_server_id(server_id),
            endpoint_id=self._resolve_endpoint_id(endpoint_id),
            payload=payload,
            deadline_ms=deadline_ms or settings.request_timeout_ms,
            maid_uuid=str(payload.get("maid_uuid") or ""),
            maid_entity_id=str(payload.get("maid_entity_id") or ""),
            direction=CLIENT_TO_JAVA,
        )
        reply = await self._send_envelope_await_reply(envelope, settings=settings)
        return reply["payload"]

    def _payload_with_default_maid(
        self,
        event_type: str,
        payload: dict[str, Any],
        settings: MaidBridgePluginSettings,
    ) -> dict[str, Any]:
        prepared = dict(payload)
        if event_type in {"maid.message.in", "maid.api.query.maid", "maid.api.call.maid_action"}:
            if not str(prepared.get("maid_uuid") or "").strip() and settings.default_maid_uuid:
                prepared["maid_uuid"] = settings.default_maid_uuid
        return prepared

    async def _send_envelope_await_reply(self, envelope: Any, *, settings: MaidBridgePluginSettings) -> dict[str, Any]:
        if self._transport is None:
            self.ctx.logger.warning("MaidBridge request not sent: transport send loop is not active")
            return {
                "type": "bridge.nack",
                "payload": {"ok": False, "error": "MaidBridge transport send loop is not active"},
            }
        future = asyncio.get_running_loop().create_future()
        self._state.add_pending(
            PendingRequest(
                request_id=envelope.id,
                trace_id=envelope.trace_id,
                deadline_ms=envelope.deadline_ms,
                future=future,
                envelope_type=envelope.type,
            )
        )
        self.ctx.logger.debug(
            f"MaidBridge pending request registered [request_id={envelope.id}, trace_id={envelope.trace_id}, "
            f"type={envelope.type}, deadline_ms={envelope.deadline_ms}]"
        )
        try:
            await self._transport.send(envelope.dumps(max_bytes=settings.max_message_bytes))
            self.ctx.logger.debug(
                f"MaidBridge envelope sent [request_id={envelope.id}, trace_id={envelope.trace_id}, type={envelope.type}]"
            )
            return await asyncio.wait_for(future, timeout=envelope.deadline_ms / 1000)
        except TimeoutError:
            self._state.pending_requests.pop(envelope.id, None)
            self.ctx.logger.warning(
                f"MaidBridge pending request timed out [request_id={envelope.id}, trace_id={envelope.trace_id}, "
                f"type={envelope.type}, deadline_ms={envelope.deadline_ms}]"
            )
            return {
                "type": "bridge.nack",
                "reply_to": envelope.id,
                "trace_id": envelope.trace_id,
                "payload": {"ok": False, "error": f"MaidBridge request {envelope.id} timed out"},
            }
        except Exception as exc:
            self._state.pending_requests.pop(envelope.id, None)
            self.ctx.logger.warning(
                f"MaidBridge request failed while awaiting reply [request_id={envelope.id}, "
                f"trace_id={envelope.trace_id}, error={exc}]"
            )
            raise

    def _complete_pending_requests(self, pending: list[PendingRequest], *, error: str) -> None:
        completed = 0
        for request in pending:
            if request.future is None or request.future.done():
                continue
            request.future.set_result(
                {
                    "type": "bridge.nack",
                    "reply_to": request.request_id,
                    "trace_id": request.trace_id,
                    "payload": {"ok": False, "error": error},
                }
            )
            completed += 1
        if completed:
            self.ctx.logger.warning(f"MaidBridge completed {completed} pending request(s) with error: {error}")

    def _gateway_route_metadata(
        self,
        primary: dict[str, Any],
        fallback: dict[str, Any],
        *,
        settings: MaidBridgePluginSettings,
    ) -> dict[str, Any]:
        merged = dict(primary)
        target = (
            merged.get("platform_io_target_group_id")
            or merged.get("target_group_id")
            or merged.get("maidbridge_room_id")
            or merged.get("room_id")
            or fallback.get("platform_io_target_group_id")
            or fallback.get("target_group_id")
            or fallback.get("maidbridge_room_id")
            or fallback.get("room_id")
            or fallback.get("scope")
            or primary.get("scope")
            or settings.server_id
        )
        merged["platform_io_account_id"] = settings.server_id
        merged["platform_io_scope"] = settings.server_id
        merged["maidbridge_endpoint_id"] = str(
            fallback.get("endpoint_id") or primary.get("endpoint_id") or settings.server_id
        )
        merged["maidbridge_room_id"] = str(target)
        merged["platform_io_target_group_id"] = str(target)
        return merged

    def _gateway_loop_error(
        self,
        *,
        message: dict[str, Any],
        route: dict[str, Any],
        metadata: dict[str, Any],
        settings: MaidBridgePluginSettings,
    ) -> str:
        values: dict[str, Any] = {}
        values.update(route)
        values.update(metadata)
        message_info = message.get("message_info")
        if isinstance(message_info, dict):
            additional_config = message_info.get("additional_config")
            if isinstance(additional_config, dict):
                values.update(additional_config)
        origin_platform = str(values.get("origin_platform") or "").strip().casefold()
        if origin_platform == "maidbridge":
            return "gateway loop rejected: origin_platform=maidbridge"
        hop_count = values.get("hop_count", 0)
        if isinstance(hop_count, bool) or not isinstance(hop_count, int):
            return "hop_count must be an integer"
        if hop_count >= settings.gateway_max_hops:
            return f"gateway loop rejected: hop_count {hop_count} exceeds limit {settings.gateway_max_hops}"
        return ""

    async def _start_runtime(
        self,
        settings: MaidBridgePluginSettings,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        room_runtime = self._room_runtime_instance()
        room_status = room_runtime.room_status()
        self.ctx.logger.info(
            f"Starting MaidBridge adapter runtime [role={self._websocket_role(settings)}, "
            f"url={self._websocket_url(settings)}, gateway={settings.enable_message_gateway}, "
            f"room_gate={settings.enable_room_gate}, rooms={len(room_status)}]"
        )
        transport = self._build_transport(settings)
        transport.on_open(lambda: self._handle_transport_open(settings, metadata=metadata or {}))
        transport.on_close(lambda: self._handle_transport_close(settings))
        maid_agent_turn_handler = (
            MaidAgentTurnService(
                ctx=self.ctx,
                settings=settings,
                send_envelope_await_reply=self._send_envelope_await_reply,
            )
            if settings.enable_maid_agent_turns
            else None
        )
        router = RuntimeRouter(
            build_runtime_bundle(
                ctx=self.ctx,
                transport=transport,
                state=self._state,
                max_message_bytes=settings.max_message_bytes,
                enable_message_gateway=settings.enable_message_gateway,
                room_runtime=room_runtime,
                maid_agent_turn_handler=maid_agent_turn_handler,
            )
        )
        self._transport = transport
        self._router = router
        try:
            await router.start()
        except Exception as exc:
            self.ctx.logger.error(f"Failed to start MaidBridge adapter runtime: {exc}")
            # 游戏端和 MaiBot 端经常分开启动，桥接未连上不能阻止插件 API/配置页注册。
            with contextlib.suppress(Exception):
                await transport.stop()
            self._transport = None
            self._router = None
            self._state.mark_disconnected()
            await self._publish_gateway_state(
                ready=False,
                metadata={
                    "enabled": True,
                    "reason": "transport_start_failed",
                    "error": str(exc),
                },
            )
            return
        self.ctx.logger.info("MaidBridge adapter runtime started")

    async def _stop_runtime(self) -> None:
        await self._cancel_room_dispatch_tasks()
        if self._router is not None:
            await self._router.stop()
        self._router = None
        self._transport = None

    def _build_transport(self, settings: MaidBridgePluginSettings) -> BridgeTransport:
        if self._transport_factory is not None:
            return self._transport_factory(settings)
        return AioHttpWebSocketBridgeTransport(
            settings.websocket_url,
            access_token=settings.access_token,
            max_message_bytes=settings.max_message_bytes,
        )

    def _websocket_role(self, settings: MaidBridgePluginSettings) -> str:
        del settings
        return "client"

    def _websocket_url(self, settings: MaidBridgePluginSettings) -> str:
        return settings.websocket_url

    async def _handle_transport_open(
        self,
        settings: MaidBridgePluginSettings,
        *,
        metadata: dict[str, Any],
    ) -> None:
        connection_id = f"{settings.server_id}@{self._websocket_url(settings)}"
        self._state.mark_connected(server_id=settings.server_id, connection_id=connection_id)
        self.ctx.logger.info(
            f"MaidBridge websocket connected [connection_id={connection_id}, role={self._websocket_role(settings)}]"
        )
        await self._send_client_hello(settings)
        await self._publish_gateway_state(
            ready=settings.enable_message_gateway,
            metadata={
                "enabled": True,
                "enable_message_gateway": bool(settings.enable_message_gateway),
                "connection_id": connection_id,
                "websocket_role": self._websocket_role(settings),
                **metadata,
            },
        )

    async def _send_client_hello(self, settings: MaidBridgePluginSettings) -> None:
        if self._transport is None:
            return
        envelope = build_client_hello_envelope(
            client_id=f"{settings.server_id}@{self._websocket_url(settings)}",
            roles=settings.client_roles,
            subscriptions=settings.subscriptions,
            deadline_ms=settings.request_timeout_ms,
        )
        await self._transport.send(envelope.dumps(max_bytes=settings.max_message_bytes))
        self.ctx.logger.info(
            f"MaidBridge client hello sent [request_id={envelope.request_id}, "
            f"roles={settings.client_roles}, subscriptions={settings.subscriptions}]"
        )

    async def _handle_transport_close(self, settings: MaidBridgePluginSettings) -> None:
        pending = self._state.mark_disconnected()
        self.ctx.logger.warning(
            f"MaidBridge websocket closed [role={self._websocket_role(settings)}, pending={len(pending)}]"
        )
        self._complete_pending_requests(pending, error="MaidBridge transport closed")
        await self._publish_gateway_state(ready=False, metadata={"reason": "transport_closed"})

    async def _publish_gateway_state(self, *, ready: bool, metadata: dict[str, Any]) -> None:
        settings = self._settings()
        await self.ctx.gateway.update_state(
            GATEWAY_NAME,
            ready=ready,
            platform=PLATFORM,
            account_id=settings.server_id,
            scope=settings.server_id,
            metadata={
                "protocol": PROTOCOL,
                "websocket_role": self._websocket_role(settings),
                "websocket_url": self._websocket_url(settings),
                "enable_message_gateway": bool(settings.enable_message_gateway),
                **metadata,
            },
        )
