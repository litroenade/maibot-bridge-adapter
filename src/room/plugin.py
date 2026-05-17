import asyncio
from typing import Any

from maibot_sdk import API, HookHandler

from .delivery import RoomDelivery
from .decision import RoomDecisionService
from .hooks import hook_abort, hook_continue, mark_room_source_message as mark_room_source_message_hook, message_additional_config
from .outbound import build_bridge_room_outbound_route, is_bridge_room_outbound, mutate_message_to_primary_target
from .recorder import RoomSourceRecorder
from .routing import resolve_room_send_policy
from .runtime import RoomRuntime


class MaidBridgeRoomPlugin:
    @HookHandler(
        "chat.receive.after_process",
        name="maidbridge_room_source_marker",
        mode="blocking",
        order="late",
        timeout_ms=3000,
    )
    async def mark_room_source_message(
        self,
        message: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        settings = self._settings()
        if not settings.enable_room_gate or not isinstance(message, dict):
            self.ctx.logger.debug("MaidBridge room gate bypassed: disabled or message is not a dict")
            return hook_continue()
        result = await mark_room_source_message_hook(
            runtime=self._room_runtime_instance(),
            message=message,
            recorder=self._room_source_recorder_instance(),
        )
        self._remember_room_source_session(result, message=message)
        self._log_room_source_result(result)
        return result

    @HookHandler(
        "send_service.after_build_message",
        name="maidbridge_room_outbound_context_marker",
        mode="blocking",
        order="early",
        timeout_ms=1000,
    )
    async def attach_room_outbound_context(
        self,
        message: dict[str, Any] | None = None,
        stream_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        settings = self._settings()
        if not settings.enable_room_gate or not isinstance(message, dict):
            return hook_continue()
        normalized_stream_id = str(stream_id or message.get("session_id") or "").strip()
        context = self._room_session_context_by_stream_id.get(normalized_stream_id)
        if context is None:
            return hook_continue()
        additional_config = self._ensure_message_additional_config(message)
        additional_config.update(context)
        self.ctx.logger.info(
            f"MaidBridge room outbound context attached [room={context['maidbridge_room_id']}, "
            f"stream={normalized_stream_id}, source_member={context['maidbridge_room_source_member_id']}, "
            f"session_platform={context['maidbridge_room_session_platform']}]"
        )
        return hook_continue(
            message=message,
            custom_result={
                "room_id": context["maidbridge_room_id"],
                "source_member_id": context["maidbridge_room_source_member_id"],
                "stream_id": normalized_stream_id,
                "session_platform": context["maidbridge_room_session_platform"],
            },
        )

    @HookHandler(
        "send_service.before_send",
        name="maidbridge_room_outbound_router",
        mode="blocking",
        order="early",
        timeout_ms=5000,
    )
    async def route_room_outbound_message(
        self,
        message: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        settings = self._settings()
        if not settings.enable_room_gate or not isinstance(message, dict) or not is_bridge_room_outbound(message):
            return hook_continue()
        room_id = ""
        source_member_id = ""
        selected_target_member_ids: list[str] = []
        routing_reason = ""
        try:
            runtime = self._room_runtime_instance()
            room_id, source_member_id, text = self._bridge_room_outbound_context(message)
            self.ctx.logger.info(
                f"MaidBridge room outbound routing start [room={room_id}, source_member={source_member_id}, "
                f"text_len={len(text)}]"
            )
            decision = await asyncio.wait_for(
                self._room_decision_service(settings=settings).decide(
                    runtime=runtime,
                    room_id=room_id,
                    text=text,
                    source_member_id=source_member_id,
                ),
                timeout=max(1.0, settings.request_timeout_ms / 1000.0),
            )
            selected_target_member_ids = decision.target_member_ids
            routing_reason = decision.reason
            self.ctx.logger.info(
                f"MaidBridge room outbound LLM routing result [room={room_id}, source_member={source_member_id}, "
                f"targets={decision.target_member_ids}, reason={decision.reason}]"
            )
            bridge_only_plan = runtime.room_send(
                room_id,
                text=text,
                target_member_ids=decision.target_member_ids,
                source_member_id=source_member_id,
            )
            if self._plan_is_bridge_only(bridge_only_plan):
                delivery_result = await self._deliver_bridge_only_room_targets(bridge_only_plan, text=text)
                delivery_success = bool(delivery_result.get("success"))
                result_reason = "bridge_only_delivered" if delivery_success else "bridge_only_failed"
                log_message = (
                    f"MaidBridge room outbound bridge-only targets handled [room={room_id}, "
                    f"source_member={source_member_id}, targets={selected_target_member_ids}, "
                    f"success={delivery_success}; aborted pseudo-platform send]"
                )
                if delivery_success:
                    self.ctx.logger.info(log_message)
                else:
                    self.ctx.logger.warning(log_message)
                custom_result = {
                    "stage": "room_outbound",
                    "reason": result_reason,
                    "room_id": room_id,
                    "source_member_id": source_member_id,
                    "target_member_ids": selected_target_member_ids,
                    "routing_reason": routing_reason,
                    "targets": selected_target_member_ids,
                    "delivery": delivery_result,
                }
                if not delivery_success:
                    custom_result["error"] = str(delivery_result.get("error") or "bridge-only room delivery failed")
                return hook_abort(message=message, custom_result=custom_result)
            route = build_bridge_room_outbound_route(
                runtime=runtime,
                message=message,
                target_member_ids=decision.target_member_ids,
            )
            mutate_message_to_primary_target(message, route)
            self._log_room_outbound_primary(route)
            return hook_continue(
                message=message,
                custom_result={
                    "room_id": route.room_id,
                    "source_member_id": route.source_member_id,
                    "target_member_ids": route.target_member_ids,
                    "primary_member_id": route.primary_target["member_id"],
                    "primary_platform": route.primary_target["platform"],
                    "extra_member_ids": [target["member_id"] for target in route.extra_targets],
                },
            )
        except asyncio.TimeoutError:
            error = "room decision timed out"
            self.ctx.logger.warning(
                f"MaidBridge room outbound routing failed [room={room_id}, source_member={source_member_id}, "
                f"error={error}; aborted pseudo-platform send]"
            )
            return hook_abort(
                message=message,
                custom_result={
                    "stage": "room_outbound",
                    "reason": "abort_pseudo_platform_send",
                    "error": error,
                    "room_id": room_id,
                    "source_member_id": source_member_id,
                    "target_member_ids": selected_target_member_ids,
                }
            )
        except (RuntimeError, ValueError) as exc:
            error = str(exc)
            self.ctx.logger.warning(
                f"MaidBridge room outbound routing failed [room={room_id}, source_member={source_member_id}, "
                f"targets={selected_target_member_ids}, routing_reason={routing_reason}, "
                f"error={error}; aborted pseudo-platform send]"
            )
            return hook_abort(
                message=message,
                custom_result={
                    "stage": "room_outbound",
                    "reason": "abort_pseudo_platform_send",
                    "error": error,
                    "room_id": room_id,
                    "source_member_id": source_member_id,
                    "target_member_ids": selected_target_member_ids,
                    "routing_reason": routing_reason,
                }
            )
        except Exception as exc:
            error = str(exc)
            self.ctx.logger.warning(
                f"MaidBridge room outbound routing failed [room={room_id}, source_member={source_member_id}, "
                f"targets={selected_target_member_ids}, routing_reason={routing_reason}, "
                f"error={error}; aborted pseudo-platform send]"
            )
            return hook_abort(
                message=message,
                custom_result={
                    "stage": "room_outbound",
                    "reason": "abort_pseudo_platform_send",
                    "error": error,
                    "room_id": room_id,
                    "source_member_id": source_member_id,
                    "target_member_ids": selected_target_member_ids,
                    "routing_reason": routing_reason,
                }
            )

    @HookHandler(
        "send_service.after_send",
        name="maidbridge_room_outbound_extra_dispatcher",
        mode="blocking",
        order="late",
        timeout_ms=5000,
    )
    async def deliver_room_outbound_extra_targets_after_send(
        self,
        message: dict[str, Any] | None = None,
        sent: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        settings = self._settings()
        if not settings.enable_room_gate or not isinstance(message, dict):
            return hook_continue()
        additional_config = message_additional_config(message)
        if additional_config.get("maidbridge_room_outbound_routed") is not True:
            return hook_continue()
        extra_member_ids = self._room_extra_member_ids(additional_config)
        if not extra_member_ids:
            return hook_continue()
        room_id = str(
            additional_config.get("maidbridge_room_route_room_id")
            or additional_config.get("maidbridge_room_id")
            or ""
        ).strip()
        source_member_id = str(
            additional_config.get("maidbridge_room_route_source_member_id")
            or additional_config.get("maidbridge_room_source_member_id")
            or ""
        ).strip()
        if not sent:
            self.ctx.logger.warning(
                f"MaidBridge room outbound extra targets skipped [room={room_id}, "
                f"source_member={source_member_id}, extra_targets={extra_member_ids}, sent=False]"
            )
            return hook_continue()
        try:
            text = self._room_outbound_text(message)
            plan = self._room_runtime_instance().room_send(
                room_id,
                text=text,
                target_member_ids=extra_member_ids,
                source_member_id=source_member_id,
            )
            self.ctx.logger.info(
                f"MaidBridge room outbound primary sent; scheduling extra targets [room={room_id}, "
                f"source_member={source_member_id}, extra_targets={extra_member_ids}, sent=True]"
            )
            self._schedule_extra_room_plan(
                plan,
                room_id=room_id,
                source_member_id=source_member_id,
                text=text,
            )
        except Exception as exc:
            self.ctx.logger.warning(
                f"MaidBridge room outbound extra target scheduling failed [room={room_id}, "
                f"source_member={source_member_id}, extra_targets={extra_member_ids}, sent=True, error={exc}]"
            )
        return hook_continue()

    @API("room_status", description="列出已配置 room 的运行状态", version="1", public=True)
    async def room_status(self) -> list[dict[str, Any]]:
        return self._room_runtime_instance().room_status()

    @API("room_members", description="列出已配置 room 的成员", version="1", public=True)
    async def room_members(self, room_id: str) -> list[dict[str, Any]]:
        return self._room_runtime_instance().room_members(room_id)

    @API(
        "room_ingest",
        description="写入结构化消息到 room 缓冲区",
        version="1",
        public=True,
    )
    async def room_ingest(
        self,
        room_id: str,
        member_id: str,
        user_id: str,
        user_display_name: str,
        text: str,
        timestamp_ms: int,
        origin_message_id: str,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._room_runtime_instance().room_ingest(
                room_id=room_id,
                member_id=member_id,
                user_id=user_id,
                user_display_name=user_display_name,
                text=text,
                timestamp_ms=timestamp_ms,
                origin_message_id=origin_message_id,
                extras=extras,
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
                "room_id": room_id,
                "member_id": member_id,
            }

    @API(
        "room_send",
        description="按已配置路由策略生成 room 发送计划",
        version="1",
        public=True,
    )
    async def room_send(
        self,
        room_id: str,
        text: str,
        target_member_ids: list[str] | None = None,
        source_member_id: str = "",
    ) -> dict[str, Any]:
        settings = self._settings()
        runtime = self._room_runtime_instance()
        try:
            policy = resolve_room_send_policy(
                target_member_ids=target_member_ids,
            )
            resolved_target_member_ids = policy.target_member_ids
            room_decision: dict[str, Any] | None = None
            if policy.use_llm_decision:
                decision = await asyncio.wait_for(
                    self._room_decision_service(settings=settings).decide(
                        runtime=runtime,
                        room_id=room_id,
                        text=text,
                        source_member_id=source_member_id,
                    ),
                    timeout=max(1.0, settings.request_timeout_ms / 1000.0),
                )
                resolved_target_member_ids = decision.target_member_ids
                room_decision = {
                    "reason": decision.reason,
                }
            plan = runtime.room_send(
                room_id,
                text=text,
                target_member_ids=resolved_target_member_ids,
                source_member_id=source_member_id,
            )
            if room_decision is not None:
                plan["room_decision"] = room_decision
        except asyncio.TimeoutError:
            return {"success": False, "error": "room decision timed out", "room_id": room_id}
        except (RuntimeError, ValueError) as exc:
            return {"success": False, "error": str(exc), "room_id": room_id}
        delivery = RoomDelivery(
            ctx=self.ctx,
            settings=self._settings(),
        )
        return await delivery.deliver_plan(plan, text=text)

    def _room_runtime_instance(self) -> RoomRuntime:
        if self._room_runtime is None:
            settings = self._settings()
            self._room_runtime = RoomRuntime(getattr(settings, "rooms", []), default_server_id=settings.server_id)
        return self._room_runtime

    def _room_decision_service(self, *, settings: Any) -> RoomDecisionService:
        # 决策服务同时被 hook 和 API 调用，用工厂隔离构造点，避免两处状态漂移。
        return RoomDecisionService(ctx=self.ctx, settings=settings)

    def _room_source_recorder_instance(self) -> RoomSourceRecorder:
        if self._room_source_recorder is None:
            self._room_source_recorder = RoomSourceRecorder()
        return self._room_source_recorder

    def _remember_room_source_session(self, result: dict[str, Any], *, message: dict[str, Any]) -> None:
        custom_result = result.get("custom_result")
        if not isinstance(custom_result, dict) or not custom_result.get("normalized_platform"):
            return
        stream_id = str(message.get("session_id") or "").strip()
        room_id = str(custom_result.get("room_id") or "").strip()
        room_name = str(custom_result.get("room_name") or room_id).strip()
        source_member_id = str(custom_result.get("source_member_id") or custom_result.get("member_id") or "").strip()
        session_platform = str(custom_result.get("normalized_platform") or "").strip()
        if not stream_id or not room_id or not source_member_id or not session_platform:
            return
        self._room_session_context_by_stream_id[stream_id] = {
            "maidbridge_room_id": room_id,
            "maidbridge_room_name": room_name or room_id,
            "maidbridge_room_source_member_id": source_member_id,
            "maidbridge_room_session_platform": session_platform,
        }

    def _ensure_message_additional_config(self, message: dict[str, Any]) -> dict[str, Any]:
        message_info = message.setdefault("message_info", {})
        if not isinstance(message_info, dict):
            message_info = {}
            message["message_info"] = message_info
        additional_config = message_info.setdefault("additional_config", {})
        if not isinstance(additional_config, dict):
            additional_config = {}
            message_info["additional_config"] = additional_config
        return additional_config

    def _bridge_room_outbound_context(self, message: dict[str, Any]) -> tuple[str, str, str]:
        message_info = message.get("message_info")
        additional_config: dict[str, Any] = {}
        group_info: dict[str, Any] = {}
        if isinstance(message_info, dict):
            raw_additional_config = message_info.get("additional_config")
            if isinstance(raw_additional_config, dict):
                additional_config = raw_additional_config
            raw_group_info = message_info.get("group_info")
            if isinstance(raw_group_info, dict):
                group_info = raw_group_info
        room_id = str(additional_config.get("maidbridge_room_id") or group_info.get("group_id") or "").strip()
        if not room_id:
            raise ValueError("bridge room outbound message is missing room id")
        source_member_id = str(additional_config.get("maidbridge_room_source_member_id") or "").strip()
        text = self._room_outbound_text(message)
        return room_id, source_member_id, text

    def _room_outbound_text(self, message: dict[str, Any]) -> str:
        for key in ("processed_plain_text", "display_message", "plain_text", "text"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise ValueError("bridge room outbound message text is empty")

    def _schedule_extra_room_plan(
        self,
        plan: dict[str, Any],
        *,
        room_id: str,
        source_member_id: str,
        text: str,
    ) -> None:
        targets = self._planned_targets(plan)
        if not targets:
            return
        self.ctx.logger.info(
            f"MaidBridge room outbound extra targets scheduled [room={room_id}, "
            f"source_member={source_member_id}, target_count={len(targets)}, "
            f"targets={self._format_room_target_details(targets)}]"
        )
        task = asyncio.create_task(
            self._deliver_extra_room_plan(
                plan,
                room_id=room_id,
                source_member_id=source_member_id,
                text=text,
            )
        )
        self._room_dispatch_tasks.add(task)
        task.add_done_callback(self._log_room_background_task_error)

    async def _deliver_extra_room_plan(
        self,
        plan: dict[str, Any],
        *,
        room_id: str,
        source_member_id: str,
        text: str,
    ) -> None:
        delivery = RoomDelivery(
            ctx=self.ctx,
            settings=self._settings(),
        )
        result = await delivery.deliver_plan(plan, text=text)
        self._log_room_outbound_extra_result(
            plan,
            room_id=room_id,
            source_member_id=source_member_id,
            result=result,
        )

    async def _deliver_bridge_only_room_targets(self, plan: dict[str, Any], *, text: str) -> dict[str, Any]:
        delivery = RoomDelivery(
            ctx=self.ctx,
            settings=self._settings(),
        )
        return await delivery.deliver_plan(plan, text=text)

    def _plan_is_bridge_only(self, plan: dict[str, Any]) -> bool:
        targets = plan.get("planned_targets")
        if not isinstance(targets, list) or not targets:
            return False
        return all(isinstance(target, dict) and _room_target_delivery(target) == "bridge" for target in targets)

    def _log_room_outbound_primary(self, route: Any) -> None:
        primary = route.primary_target
        self.ctx.logger.info(
            f"MaidBridge room outbound primary target [room={route.room_id}, source_member={route.source_member_id}, "
            f"target_member={primary['member_id']}, platform={primary['platform']}, "
            f"group={self._room_target_group_id(primary)}, extras={len(route.extra_targets)}]"
        )

    def _log_room_outbound_extra_result(
        self,
        plan: dict[str, Any],
        *,
        room_id: str,
        source_member_id: str,
        result: dict[str, Any],
    ) -> None:
        targets = self._planned_targets(plan)
        target_details = self._format_room_target_details(targets)
        if result.get("success"):
            self.ctx.logger.info(
                f"MaidBridge room outbound extra targets delivered [room={room_id}, "
                f"source_member={source_member_id}, target_count={len(targets)}, targets={target_details}]"
            )
            return
        self.ctx.logger.warning(
            f"MaidBridge room outbound extra targets failed [room={room_id}, "
            f"source_member={source_member_id}, target_count={len(targets)}, targets={target_details}, "
            f"error={result.get('error') or 'unknown'}, result={result}]"
        )

    def _room_extra_member_ids(self, additional_config: dict[str, Any]) -> list[str]:
        raw_ids = additional_config.get("maidbridge_room_route_extra_member_ids")
        if not isinstance(raw_ids, list):
            return []
        member_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in raw_ids:
            member_id = str(raw_id or "").strip()
            if not member_id or member_id in seen:
                continue
            member_ids.append(member_id)
            seen.add(member_id)
        return member_ids

    def _planned_targets(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        targets = plan.get("planned_targets")
        if not isinstance(targets, list):
            return []
        return [target for target in targets if isinstance(target, dict)]

    def _format_room_target_details(self, targets: list[dict[str, Any]]) -> str:
        details = []
        for target in targets:
            details.append(
                f"{target.get('member_id') or ''}/{target.get('platform') or ''}/{self._room_target_group_id(target)}"
            )
        return ", ".join(details)

    def _room_target_group_id(self, target: dict[str, Any]) -> str:
        endpoint = target.get("endpoint")
        if isinstance(endpoint, dict):
            return str(endpoint.get("channel_id") or endpoint.get("group_id") or "").strip()
        return ""

    def _log_room_source_result(self, result: dict[str, Any]) -> None:
        if result.get("action") != "abort":
            custom_result = result.get("custom_result")
            if isinstance(custom_result, dict) and custom_result.get("fail_open"):
                room_id = str(custom_result.get("room_id") or "")
                member_id = str(custom_result.get("member_id") or "")
                stage = str(custom_result.get("stage") or "unknown")
                self.ctx.logger.warning(
                    f"MaidBridge room source failed open [room={room_id}, member={member_id}, stage={stage}]"
                )
                self.ctx.logger.debug(f"MaidBridge room source failed open detail: {custom_result}")
                return
            if isinstance(custom_result, dict) and custom_result.get("normalized_platform"):
                self.ctx.logger.info(
                    f"MaidBridge room source normalized [room={custom_result.get('room_id') or ''}, "
                    f"room_name={custom_result.get('room_name') or ''}, "
                    f"source_member={custom_result.get('source_member_id') or custom_result.get('member_id') or ''}, "
                    f"source_platform={custom_result.get('source_platform') or ''}, "
                    f"source_group={custom_result.get('source_group_id') or ''}, "
                    f"source_group_name={custom_result.get('source_group_name') or ''}, "
                    f"platform={custom_result.get('normalized_platform') or ''}]"
                )
                return
            self.ctx.logger.debug("MaidBridge room gate passed through without source-room match")
            return
        self.ctx.logger.warning(f"MaidBridge room source returned unexpected abort result: {result}")

    def _log_room_background_task_error(self, task: asyncio.Task[None]) -> None:
        self._room_dispatch_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.ctx.logger.warning(f"MaidBridge room background task failed: {exc}")

    async def _cancel_room_dispatch_tasks(self) -> None:
        tasks = list(self._room_dispatch_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._room_dispatch_tasks.clear()


def _room_target_delivery(target: dict[str, Any]) -> str:
    intent = target.get("intent")
    if not isinstance(intent, dict):
        return ""
    return str(intent.get("delivery") or "").strip()
