from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..constants import CLIENT_TO_JAVA
from ..maid.protocol.envelope import build_ai_event_envelope


class RoomDelivery:
    def __init__(
        self,
        *,
        ctx: Any,
        state: Any,
        transport: Any,
        settings: Any,
        send_envelope_await_reply: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        self._ctx = ctx
        self._state = state
        self._transport = transport
        self._settings = settings
        self._send_envelope_await_reply = send_envelope_await_reply

    async def deliver_plan(self, plan: dict[str, Any], *, text: str) -> dict[str, Any]:
        delivery_results = []
        for target in plan["planned_targets"]:
            try:
                if _target_delivery(target) == "bridge":
                    result = await self._deliver_maidbridge_target(target)
                else:
                    result = await self._deliver_sdk_target(target, text=text)
            except Exception as exc:
                result = _delivery_failure(target, str(exc))
            delivery_results.append(result)
        sent_targets = [result for result in delivery_results if result["success"]]
        success = len(sent_targets) == len(delivery_results)
        return {
            **plan,
            "success": success,
            "delivery_results": delivery_results,
            "sent_targets": sent_targets,
            **({} if success else {"error": "one or more room targets failed"}),
        }

    async def _deliver_maidbridge_target(self, target: dict[str, Any]) -> dict[str, Any]:
        if not self._state.ready:
            return _delivery_failure(target, "MaidBridge transport is not connected")
        if self._transport is None:
            return _delivery_failure(target, "MaidBridge transport send loop is not active")
        frame = target["frame"]
        request_id = f"room-{uuid4()}"
        envelope = build_ai_event_envelope(
            event_type=frame["type"],
            event_id=request_id,
            request_id=request_id,
            trace_id=f"trace-{uuid4()}",
            server_id=str(frame.get("server_id") or self._settings.server_id),
            endpoint_id=frame["endpoint_id"],
            payload=frame["payload"],
            deadline_ms=self._settings.request_timeout_ms,
            maid_uuid=frame.get("maid_uuid", ""),
            maid_entity_id=frame.get("maid_entity_id", ""),
            direction=frame.get("direction", CLIENT_TO_JAVA),
        )
        reply = await self._send_envelope_await_reply(envelope, settings=self._settings)
        payload = reply["payload"]
        if reply["type"] == "bridge.ack" and payload.get("ok", True):
            return {
                "member_id": target["member_id"],
                "platform": target["platform"],
                "platform_label": target["platform_label"],
                "success": True,
                "external_message_id": envelope.id,
                "trace_id": envelope.trace_id,
                "ack": payload,
            }
        return {
            "member_id": target["member_id"],
            "platform": target["platform"],
            "platform_label": target["platform_label"],
            "success": False,
            "trace_id": envelope.trace_id,
            "error": str(payload.get("error") or "MaidBridge room send was rejected"),
        }

    async def _deliver_sdk_target(self, target: dict[str, Any], *, text: str) -> dict[str, Any]:
        adapter_result = await self._deliver_adapter_api_target(target)
        if adapter_result is not None:
            return adapter_result
        candidates = _sdk_stream_candidates(target)
        if not candidates:
            return _delivery_failure(target, "room target is missing a stream lookup id")
        stream_id = ""
        platform = ""
        tried: list[str] = []
        for candidate_platform, group_id in candidates:
            tried.append(f"{candidate_platform}:{group_id}")
            stream = await self._ctx.chat.get_stream_by_group_id(group_id, platform=candidate_platform)
            stream_id = _extract_stream_id(stream)
            if stream_id:
                platform = candidate_platform
                break
        if not stream_id:
            return _delivery_failure(target, f"no SDK chat stream found for {', '.join(tried)}")
        sent = await self._ctx.send.text(text, stream_id)
        send_error = _sdk_send_error(sent, stream_id=stream_id)
        if send_error:
            return _delivery_failure(target, send_error)
        return {
            "member_id": target["member_id"],
            "platform": target["platform"],
            "platform_label": target["platform_label"],
            "success": True,
            "sdk_platform": platform,
            "stream_id": stream_id,
        }

    async def _deliver_adapter_api_target(self, target: dict[str, Any]) -> dict[str, Any] | None:
        api = getattr(self._ctx, "api", None)
        list_api = getattr(api, "list", None)
        call_api = getattr(api, "call", None)
        if not callable(list_api) or not callable(call_api):
            return None
        candidates = _adapter_api_candidates(target)
        if not candidates:
            return None
        visible_api_names = await _visible_adapter_api_names(list_api)
        if not visible_api_names:
            return None
        matched_candidates = [
            candidate
            for candidate in candidates
            if candidate["api_name"] in visible_api_names
        ]
        if not matched_candidates:
            return None
        errors: list[str] = []
        for candidate in matched_candidates:
            api_name = candidate["api_name"]
            version = candidate.get("version", "")
            try:
                result = await _maybe_await(call_api(api_name, version=version, **candidate["kwargs"]))
            except Exception as exc:
                errors.append(f"{api_name}: {exc}")
                continue
            error = _adapter_api_result_error(result, api_name=api_name)
            if error:
                errors.append(error)
                continue
            return {
                "member_id": target["member_id"],
                "platform": target["platform"],
                "platform_label": target["platform_label"],
                "success": True,
                "adapter_api": api_name,
                "adapter_api_version": version,
                "adapter_result": result,
            }
        return _delivery_failure(target, f"adapter API delivery failed: {'; '.join(errors)}")


async def _visible_adapter_api_names(list_api: Callable[[], Any]) -> set[str]:
    try:
        listed = await _maybe_await(list_api())
    except Exception:
        return set()
    if isinstance(listed, dict):
        raw_items = listed.get("apis") or listed.get("api_names") or listed.get("data") or []
    else:
        raw_items = listed
    names: set[str] = set()
    if not isinstance(raw_items, list | tuple | set):
        return names
    for item in raw_items:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("api_name") or "").strip()
        else:
            name = str(getattr(item, "name", "") or getattr(item, "api_name", "")).strip()
        if name:
            names.add(name)
    return names


async def _maybe_await(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value


def _adapter_api_candidates(target: dict[str, Any]) -> list[dict[str, Any]]:
    intent = _target_intent(target)
    raw_candidates = intent.get("adapter_apis", [])
    if not isinstance(raw_candidates, list):
        raise ValueError("room target intent.adapter_apis must be a list")
    candidates: list[dict[str, Any]] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            raise ValueError("room target intent.adapter_apis entries must be objects")
        api_name = str(raw_candidate.get("api_name") or "").strip()
        if api_name:
            candidates.append(
                {
                    "api_name": api_name,
                    "version": str(raw_candidate.get("version") or "").strip(),
                    "kwargs": dict(raw_candidate.get("kwargs") or {}),
                }
            )
    return candidates


def _sdk_stream_candidates(target: dict[str, Any]) -> list[tuple[str, str]]:
    intent = _target_intent(target)
    raw_streams = intent.get("sdk_streams", [])
    if not isinstance(raw_streams, list):
        raise ValueError("room target intent.sdk_streams must be a list")
    candidates: list[tuple[str, str]] = []
    for raw_stream in raw_streams:
        if not isinstance(raw_stream, dict):
            raise ValueError("room target intent.sdk_streams entries must be objects")
        platform = str(raw_stream.get("platform") or "").strip()
        group_id = str(raw_stream.get("group_id") or "").strip()
        if platform and group_id:
            candidates.append((platform, group_id))
    return candidates


def _target_intent(target: dict[str, Any]) -> dict[str, Any]:
    intent = target.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("room target is missing outbound intent")
    return intent


def _target_delivery(target: dict[str, Any]) -> str:
    """发送层只读 runtime 生成的 intent，不再根据 platform 二次推断投递方式。"""
    delivery = str(_target_intent(target).get("delivery") or "").strip()
    if delivery not in {"sdk", "bridge"}:
        raise ValueError("room target intent.delivery must be 'sdk' or 'bridge'")
    return delivery


def _delivery_failure(target: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "member_id": target["member_id"],
        "platform": target["platform"],
        "platform_label": target["platform_label"],
        "success": False,
        "error": error,
    }


def _extract_stream_id(stream: Any) -> str:
    if isinstance(stream, dict):
        nested_stream = stream.get("stream")
        if isinstance(nested_stream, dict):
            return _extract_stream_id(nested_stream)
        return str(stream.get("stream_id") or stream.get("session_id") or "").strip()
    return str(getattr(stream, "stream_id", "") or getattr(stream, "session_id", "") or "").strip()


def _sdk_send_error(result: Any, *, stream_id: str) -> str:
    if isinstance(result, dict):
        if result.get("success") is True:
            return ""
        return str(result.get("error") or f"SDK send.text failed for stream {stream_id}")
    if result:
        return ""
    return f"SDK send.text failed for stream {stream_id}"


def _adapter_api_result_error(result: Any, *, api_name: str) -> str:
    if not isinstance(result, dict):
        return "" if result else f"{api_name}: empty result"
    if result.get("success") is False or result.get("ok") is False:
        return str(result.get("error") or result.get("message") or f"{api_name}: returned error")
    if result.get("error"):
        return str(result["error"])
    return ""


__all__ = ["RoomDelivery"]
