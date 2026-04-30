"""外部女仆回合处理。

Java mod 已在服务运行前取消 TouhouLittleMaid 原生 AIChat 回合，因此这里返回
完整回合结果，而不是 LLM 站点覆写。
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from ..constants import CLIENT_TO_JAVA, DEFAULT_JAVA_ENDPOINT_ID
from .protocol.envelope import BridgeEnvelope, build_ai_event_envelope


SendEnvelopeAwaitReply = Callable[..., Awaitable[dict[str, Any]]]

_ACTION_TOOL_NAMES = frozenset(
    {
        "switch_sit",
        "switch_follow_state",
        "switch_schedule",
        "switch_work_task",
    }
)


class MaidAgentTurnService:
    """把单个 `maid.agent.turn.request` 信封转为 `maid.agent.turn.result`。

    外部 agent 边界刻意收窄：Python 只返回聊天文本、白名单动作意图和关联元数据。
    实体查询、权限检查、动作执行和历史写入依赖实时 Minecraft 状态，仍由 Java 负责。
    """

    def __init__(
        self,
        *,
        ctx: Any,
        settings: Any,
        send_envelope_await_reply: SendEnvelopeAwaitReply,
    ) -> None:
        self._ctx = ctx
        self._settings = settings
        self._send_envelope_await_reply = send_envelope_await_reply

    async def handle(self, envelope: BridgeEnvelope) -> dict[str, Any]:
        # 保持提示词协议紧凑且边界明确：Java 负责实体查询和动作执行，
        # Python 只决定聊天文本和白名单动作。
        prompt = _build_prompt(
            envelope,
            channel_name=str(getattr(self._settings, "maid_channel_name", "maid") or "maid"),
            channel_id=str(getattr(self._settings, "maid_channel_id", "") or ""),
        )
        api_client = MaidBridgeJavaApiClient(
            settings=self._settings,
            send_envelope_await_reply=self._send_envelope_await_reply,
        )
        result = await _run_agent_turn_with_maid_tools(
            ctx=self._ctx,
            settings=self._settings,
            envelope=envelope,
            prompt=prompt,
            api_client=api_client,
        )
        turn_result = _build_turn_result_payload(
            envelope,
            result,
            history_policy=str(getattr(self._settings, "maid_agent_history_policy", "append") or "append"),
        )
        result_envelope = build_ai_event_envelope(
            event_type="maid.agent.turn.result",
            event_id=envelope.request_id or envelope.id,
            request_id=envelope.request_id or envelope.id,
            trace_id=envelope.trace_id,
            server_id=envelope.server_id,
            endpoint_id=envelope.endpoint_id,
            payload=turn_result,
            causation_id=envelope.id,
            parent_id=envelope.id,
            deadline_ms=envelope.deadline_ms or int(getattr(self._settings, "request_timeout_ms", 30000)),
            maid_uuid=str(turn_result.get("maid_uuid") or ""),
            maid_entity_id=str(turn_result.get("maid_entity_id") or ""),
            player_uuid=envelope.player_uuid,
            owner_uuid=envelope.owner_uuid,
            dimension=envelope.dimension,
            room_id=envelope.room_id or envelope.endpoint_id,
            visibility=envelope.visibility,
            direction=CLIENT_TO_JAVA,
            maid_agent_handling="external",
        )
        return await self._send_envelope_await_reply(result_envelope, settings=self._settings)


class MaidBridgeJavaApiClient:
    def __init__(
        self,
        *,
        settings: Any,
        send_envelope_await_reply: SendEnvelopeAwaitReply,
    ) -> None:
        self._settings = settings
        self._send_envelope_await_reply = send_envelope_await_reply

    async def query_maid_tool_schema(
        self,
        *,
        maid_uuid: str,
        maid_entity_id: str = "",
        trace_id: str = "",
        endpoint_id: str = "",
    ) -> dict[str, Any]:
        return await self._send_api_request(
            "maid.api.query.maid_tool_schema",
            maid_uuid=maid_uuid,
            maid_entity_id=maid_entity_id,
            trace_id=trace_id,
            endpoint_id=endpoint_id,
            payload={},
        )

    async def call_maid_tool(
        self,
        *,
        maid_uuid: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        maid_entity_id: str = "",
        trace_id: str = "",
        endpoint_id: str = "",
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        tool_name = _first_non_blank(tool_name)
        if not tool_name:
            raise ValueError("tool_name must be present")
        return await self._send_api_request(
            "maid.api.call.maid_tool",
            maid_uuid=maid_uuid,
            maid_entity_id=maid_entity_id,
            trace_id=trace_id,
            endpoint_id=endpoint_id,
            payload={"tool_id": tool_name, "arguments": dict(arguments), "tool_call_id": tool_call_id},
        )

    async def _send_api_request(
        self,
        event_type: str,
        *,
        maid_uuid: str,
        maid_entity_id: str,
        trace_id: str,
        endpoint_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        maid_uuid = _first_non_blank(maid_uuid)
        maid_entity_id = _first_non_blank(maid_entity_id)
        if not maid_uuid and not maid_entity_id:
            raise ValueError("maid_uuid or maid_entity_id must be present")
        endpoint_id = _first_non_blank(endpoint_id, DEFAULT_JAVA_ENDPOINT_ID)
        request_id = f"api-{uuid4()}"
        api_payload = {
            **dict(payload),
            "maid_uuid": maid_uuid,
            "maid_entity_id": maid_entity_id,
        }
        envelope = build_ai_event_envelope(
            event_type=event_type,
            event_id=request_id,
            request_id=request_id,
            trace_id=_first_non_blank(trace_id, f"trace-{uuid4()}"),
            server_id=str(getattr(self._settings, "server_id", "") or ""),
            endpoint_id=endpoint_id,
            payload=api_payload,
            deadline_ms=int(getattr(self._settings, "request_timeout_ms", 30000)),
            maid_uuid=maid_uuid,
            maid_entity_id=maid_entity_id,
            direction=CLIENT_TO_JAVA,
            maid_agent_handling="external",
        )
        return await self._send_envelope_await_reply(envelope, settings=self._settings)


async def _run_agent_turn_with_maid_tools(
    *,
    ctx: Any,
    settings: Any,
    envelope: BridgeEnvelope,
    prompt: list[dict[str, Any]],
    api_client: MaidBridgeJavaApiClient,
) -> dict[str, Any]:
    maid = envelope.payload.get("maid") if isinstance(envelope.payload.get("maid"), Mapping) else {}
    maid_uuid = _first_non_blank(envelope.maid_uuid, maid.get("uuid"), envelope.payload.get("maid_uuid"))
    maid_entity_id = _first_non_blank(
        envelope.maid_entity_id,
        maid.get("entity_id"),
        envelope.payload.get("maid_entity_id"),
    )
    schema_reply = await api_client.query_maid_tool_schema(
        maid_uuid=maid_uuid,
        maid_entity_id=maid_entity_id,
        trace_id=envelope.trace_id,
        endpoint_id=envelope.endpoint_id,
    )
    tools = [*_maid_action_tools(), *_maid_tools_from_schema_reply(schema_reply)]
    tool_results: list[dict[str, Any]] = []
    current_prompt = list(prompt)
    max_tool_rounds = int(getattr(settings, "maid_agent_max_tool_rounds", 3) or 3)

    for _ in range(max_tool_rounds + 1):
        result = await ctx.llm.generate_with_tools(
            current_prompt,
            tools,
            model=str(getattr(settings, "maid_agent_model", "replyer") or "replyer"),
            temperature=float(getattr(settings, "maid_agent_temperature", 0.3)),
            max_tokens=int(getattr(settings, "maid_agent_max_tokens", 1200)),
        )
        if result.get("success") is False:
            return result

        maid_tool_calls = _maid_tool_calls(result.get("tool_calls", []))
        if not maid_tool_calls:
            merged = dict(result)
            if tool_results:
                merged["tool_results"] = [*tool_results, *_copy_list_field(result, "tool_results")]
            return merged

        if len(tool_results) >= max_tool_rounds:
            raise RuntimeError("maid agent exceeded max maid tool rounds")
        current_prompt.append(_assistant_tool_call_message(result, maid_tool_calls))
        for tool_call in maid_tool_calls:
            tool_result = await _call_maid_tool(
                api_client=api_client,
                envelope=envelope,
                tool_call=tool_call,
                maid_uuid=maid_uuid,
                maid_entity_id=maid_entity_id,
            )
            tool_results.append(tool_result)
            current_prompt.append(_tool_result_message(tool_result))

    raise RuntimeError("maid agent exceeded max maid tool rounds")


def _maid_tools_from_schema_reply(reply: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = _reply_payload(reply)
    schemas = payload.get("schemas")
    if schemas is None:
        return []
    if not isinstance(schemas, list):
        raise ValueError("maid tool schema reply schemas must be a list")
    tools: list[dict[str, Any]] = []
    action_names = set(_ACTION_TOOL_NAMES)
    for schema in schemas:
        if not isinstance(schema, Mapping):
            raise ValueError("maid tool schema item must be an object")
        tool_id = _first_non_blank(schema.get("id"), schema.get("name"))
        if not tool_id or tool_id in action_names:
            continue
        parameters = schema.get("parameters")
        if not isinstance(parameters, Mapping):
            parameters = {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_id,
                    "description": _first_non_blank(schema.get("summary"), schema.get("description")),
                    "parameters": dict(parameters),
                },
            }
        )
    return tools


async def _call_maid_tool(
    *,
    api_client: MaidBridgeJavaApiClient,
    envelope: BridgeEnvelope,
    tool_call: Mapping[str, Any],
    maid_uuid: str,
    maid_entity_id: str,
) -> dict[str, Any]:
    tool_id = _tool_call_name(tool_call)
    tool_call_id = _tool_call_id(tool_call)
    reply = await api_client.call_maid_tool(
        maid_uuid=maid_uuid,
        maid_entity_id=maid_entity_id,
        trace_id=envelope.trace_id,
        endpoint_id=envelope.endpoint_id,
        tool_name=tool_id,
        tool_call_id=tool_call_id,
        arguments=_tool_call_arguments(tool_call),
    )
    payload = _reply_payload(reply)
    return {
        "tool_call_id": _first_non_blank(payload.get("tool_call_id"), tool_call_id),
        "tool_id": _first_non_blank(payload.get("tool_id"), tool_id),
        "result": payload.get("result", payload),
    }


def _reply_payload(reply: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(reply, Mapping):
        raise ValueError("bridge reply must be an object")
    payload = reply.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("bridge reply payload must be an object")
    if reply.get("type") == "bridge.nack" or payload.get("ok") is False:
        raise RuntimeError(str(payload.get("error") or "bridge request failed"))
    return dict(payload)


def _assistant_tool_call_message(result: Mapping[str, Any], tool_calls: list[Mapping[str, Any]]) -> dict[str, Any]:
    message = {
        "role": "assistant",
        "tool_calls": [dict(tool_call) for tool_call in tool_calls],
    }
    content = _first_non_blank(
        result.get("response"),
        result.get("content"),
        result.get("text"),
        _nested_message_content(result.get("message")),
    )
    if content:
        message["content"] = content
    return message


def _tool_result_message(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": _first_non_blank(tool_result.get("tool_call_id")),
        "content": json.dumps(tool_result, ensure_ascii=False, separators=(",", ":")),
    }


def _maid_tool_calls(raw_tool_calls: Any) -> list[Mapping[str, Any]]:
    if raw_tool_calls in (None, []):
        return []
    if not isinstance(raw_tool_calls, list):
        raise ValueError("tool_calls must be a list")
    calls: list[Mapping[str, Any]] = []
    for call in raw_tool_calls:
        if not isinstance(call, Mapping):
            raise ValueError("tool call must be an object")
        if _tool_call_name(call) not in _ACTION_TOOL_NAMES:
            calls.append(call)
    return calls


def _tool_call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, Mapping):
        return _first_non_blank(function.get("name"))
    return _first_non_blank(call.get("name"), call.get("func_name"))


def _tool_call_id(call: Mapping[str, Any]) -> str:
    return _first_non_blank(call.get("id"), call.get("call_id"), f"maidbridge-tool-{uuid4()}")


def _tool_call_arguments(call: Mapping[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    if isinstance(function, Mapping):
        return _parse_arguments(function.get("arguments", {}))
    return _parse_arguments(call.get("arguments", call.get("args", {})))


def _build_turn_result_payload(
    envelope: BridgeEnvelope,
    result: Mapping[str, Any],
    *,
    history_policy: str,
) -> dict[str, Any]:
    """构建面向 Java 的回合结果，不夹带执行状态。

    LLM 可以直接返回文本、JSON 文本或工具调用，但载荷保持声明式，
    由 Java 按当前注册表快照拒绝非法动作。
    """
    if result.get("success") is False:
        reason = result.get("error") or result.get("reason") or "MaiBot LLM returned success=false"
        raise RuntimeError(str(reason))
    chat_text, structured_actions = _extract_chat_text_and_actions(result)
    actions = [*_actions_from_tool_calls(result.get("tool_calls", [])), *structured_actions]
    maid = envelope.payload.get("maid") if isinstance(envelope.payload.get("maid"), Mapping) else {}
    turn = envelope.payload.get("turn") if isinstance(envelope.payload.get("turn"), Mapping) else {}
    maid_uuid = _first_non_blank(envelope.maid_uuid, maid.get("uuid"), envelope.payload.get("maid_uuid"))
    maid_entity_id = _first_non_blank(
        envelope.maid_entity_id,
        maid.get("entity_id"),
        envelope.payload.get("maid_entity_id"),
    )
    turn_id = _first_non_blank(turn.get("id"), envelope.payload.get("turn_id"), envelope.request_id, envelope.id)
    if not maid_uuid and not maid_entity_id:
        raise ValueError("maid_uuid or maid_entity_id must be present")
    if not turn_id:
        raise ValueError("turn_id must be present")
    return {
        "turn_id": turn_id,
        "request_id": envelope.request_id or envelope.id,
        "maid_uuid": maid_uuid,
        "maid_entity_id": maid_entity_id,
        "reply": {
            "chat_text": chat_text,
            "tts_text": chat_text,
        },
        "history": {
            "policy": _history_policy(history_policy),
        },
        "actions": actions,
        "tool_calls": _copy_list_field(result, "tool_calls"),
        "tool_results": _copy_list_field(result, "tool_results"),
    }


def _copy_list_field(result: Mapping[str, Any], field_name: str) -> list[Any]:
    value = result.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [dict(item) if isinstance(item, Mapping) else item for item in value]


def _extract_chat_text_and_actions(result: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    raw_text = _first_non_blank(
        result.get("response"),
        result.get("content"),
        result.get("text"),
        _nested_message_content(result.get("message")),
    )
    if raw_text.startswith("{"):
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            chat_text = _first_non_blank(parsed.get("chat_text"), parsed.get("text"), parsed.get("response"))
            if not chat_text:
                raise ValueError("structured maid agent response must include chat_text")
            return chat_text, _actions_from_objects(parsed.get("actions", []))
    if not raw_text:
        raise ValueError("maid agent response text must be non-empty")
    return raw_text, []


def _nested_message_content(message: Any) -> str:
    if not isinstance(message, Mapping):
        return ""
    return _first_non_blank(message.get("content"), message.get("text"))


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, Mapping):
        return _first_non_blank(message.get("text"), message.get("chat_text"), message.get("content"))
    return ""


def _actions_from_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
    if raw_tool_calls is None:
        return []
    if not isinstance(raw_tool_calls, list):
        raise ValueError("tool_calls must be a list")
    actions: list[dict[str, Any]] = []
    for call in raw_tool_calls:
        if not isinstance(call, Mapping):
            raise ValueError("tool call must be an object")
        function = call.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tool call function must be an object")
        name = _canonical_action_type(function.get("name"))
        arguments = _parse_arguments(function.get("arguments"))
        actions.append({"type": name, **arguments})
    return actions


def _actions_from_objects(raw_actions: Any) -> list[dict[str, Any]]:
    if raw_actions is None:
        return []
    if not isinstance(raw_actions, list):
        raise ValueError("actions must be a list")
    actions: list[dict[str, Any]] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, Mapping):
            raise ValueError("action must be an object")
        action = dict(raw_action)
        action["type"] = _canonical_action_type(action.get("type") or action.get("name") or action.get("action"))
        actions.append(action)
    return actions


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        parsed = dict(arguments)
    elif isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("tool call arguments must be valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("tool call arguments must decode to an object")
        parsed = dict(decoded)
    else:
        raise ValueError("tool call arguments must be an object or JSON string")
    return parsed


def _canonical_action_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(".", "_")
    normalized = {
        "sit": "switch_sit",
        "set_sit": "switch_sit",
        "sitting": "switch_sit",
        "follow": "switch_follow_state",
        "set_follow": "switch_follow_state",
        "following": "switch_follow_state",
        "schedule": "switch_schedule",
        "set_schedule": "switch_schedule",
        "task": "switch_work_task",
        "work": "switch_work_task",
        "work_task": "switch_work_task",
        "set_task": "switch_work_task",
    }.get(raw, raw)
    if normalized not in _ACTION_TOOL_NAMES:
        raise ValueError(f"unsupported maid action type: {raw or '<empty>'}")
    return normalized


def _history_policy(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"append", "none", "skip"}:
        return normalized
    raise ValueError(f"unsupported maid agent history policy: {value}")


def _first_non_blank(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _build_prompt(
    envelope: BridgeEnvelope,
    *,
    channel_name: str = "maid",
    channel_id: str = "",
) -> list[dict[str, Any]]:
    payload = envelope.payload
    maid = payload.get("maid") if isinstance(payload.get("maid"), Mapping) else {}
    message = payload.get("message")
    maid_name = _first_non_blank(maid.get("name"), payload.get("maid_name"), "maid")
    sender = payload.get("sender") if isinstance(payload.get("sender"), Mapping) else {}
    sender_name = _first_non_blank(sender.get("name") if isinstance(sender, Mapping) else "", "player")
    context = {
        "maid": {
            "uuid": _first_non_blank(envelope.maid_uuid, maid.get("uuid"), payload.get("maid_uuid")),
            "entity_id": _first_non_blank(envelope.maid_entity_id, maid.get("entity_id"), payload.get("maid_entity_id")),
            "name": maid_name,
            "model_id": payload.get("maid_model_id", ""),
            "sound_pack_id": payload.get("maid_sound_pack_id", ""),
        },
        "sender": {
            "uuid": sender.get("uuid", "") if isinstance(sender, Mapping) else "",
            "name": sender_name,
        },
        "client": payload.get("client", {}),
        "channel": {
            "name": _first_non_blank(channel_name, "maid"),
            "id": _first_non_blank(channel_id),
        },
        "message": _message_text(message),
    }
    registry_summary = payload.get("registry_summary")
    if isinstance(registry_summary, Mapping):
        context["registry_summary"] = registry_summary
    return [
        {
            "role": "system",
            "content": (
                "You are controlling one TouhouLittleMaid turn from outside Minecraft. "
                "Reply as the maid in natural chat text. Use registry_summary to choose valid work/task ids when present. "
                "Use tools only when a Minecraft maid action is required."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _maid_action_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "switch_sit",
                "description": "Make the maid sit or stand.",
                "parameters": {
                    "type": "object",
                    "properties": {"sit": {"type": "boolean"}},
                    "required": ["sit"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "switch_follow_state",
                "description": "Make the maid follow the owner or stay at home.",
                "parameters": {
                    "type": "object",
                    "properties": {"follow": {"type": "boolean"}},
                    "required": ["follow"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "switch_schedule",
                "description": "Switch the maid schedule enum by id.",
                "parameters": {
                    "type": "object",
                    "properties": {"schedule": {"type": "string"}},
                    "required": ["schedule"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "switch_work_task",
                "description": "Switch the maid work task by task id.",
                "parameters": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
        },
    ]
