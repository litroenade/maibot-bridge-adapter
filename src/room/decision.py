import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import render_room_context


_BRIDGE_ROOM_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "zh-CN" / "bridge_room_topic_analysis.prompt"
)


@dataclass(frozen=True)
class RoomDecision:
    target_member_ids: list[str]
    reason: str = ""


class RoomDecisionService:
    def __init__(self, *, ctx: Any, settings: Any) -> None:
        self._ctx = ctx
        self._settings = settings

    async def decide(
        self,
        *,
        runtime: Any,
        room_id: str,
        text: str,
        source_member_id: str = "",
    ) -> RoomDecision:
        members = runtime.room_members(room_id)
        candidates = _writable_candidates(members)
        if not candidates:
            raise ValueError("room has no writable target candidates")
        messages_block = render_room_context(
            runtime.messages_for_room(room_id),
            max_messages=int(getattr(self._settings, "room_decision_max_context_messages", 20)),
        )
        result = await self._ctx.llm.generate(
            _decision_prompt(
                room_id=room_id,
                text=text,
                source_member_id=source_member_id,
                candidates=candidates,
                messages_block=messages_block,
            ),
            model=str(getattr(self._settings, "room_decision_model", "replyer") or "replyer"),
            temperature=float(getattr(self._settings, "room_decision_temperature", 0.1)),
            max_tokens=int(getattr(self._settings, "room_decision_max_tokens", 256)),
        )
        if not isinstance(result, Mapping):
            raise ValueError("room decision LLM result must be an object")
        if result.get("success") is False:
            reason = result.get("error") or result.get("reason") or "room decision LLM returned success=false"
            raise RuntimeError(str(reason))
        decision = _parse_decision(result)
        allowed = {candidate["member_id"] for candidate in candidates}
        invalid = [member_id for member_id in decision.target_member_ids if member_id not in allowed]
        if invalid:
            raise ValueError(f"room decision selected non-writable targets: {', '.join(invalid)}")
        if not decision.target_member_ids:
            raise ValueError("room decision selected no targets")
        return decision


def _writable_candidates(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for member in members:
        if not member.get("can_write", True):
            continue
        member_id = str(member.get("member_id") or "").strip()
        if not member_id:
            continue
        candidates.append(
            {
                "member_id": member_id,
                "platform": member.get("platform", ""),
                "platform_key": member.get("platform_key", member.get("platform", "")),
                "platform_label": member.get("platform_label", ""),
                "display_name": member.get("display_name", ""),
                "group_name": member.get("group_name", ""),
            }
        )
    return candidates


def _decision_prompt(
    *,
    room_id: str,
    text: str,
    source_member_id: str,
    candidates: list[dict[str, Any]],
    messages_block: str,
) -> list[dict[str, str]]:
    payload = {
        "room_id": room_id,
        "source_member_id": source_member_id,
        "outbound_text": text,
        "candidates": candidates,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return [
        {
            "role": "system",
            "content": _render_bridge_room_prompt(messages_block=messages_block),
        },
        {
            "role": "user",
            "content": payload_json,
        },
    ]


def _render_bridge_room_prompt(*, messages_block: str) -> str:
    return _load_bridge_room_prompt().format(
        history_topics_block="（暂无历史话题）",
        messages_block=messages_block,
    )


def _load_bridge_room_prompt() -> str:
    try:
        prompt = _BRIDGE_ROOM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"bridge room prompt file is missing: {_BRIDGE_ROOM_PROMPT_PATH}") from exc
    if not prompt:
        raise ValueError(f"bridge room prompt file is empty: {_BRIDGE_ROOM_PROMPT_PATH}")
    return prompt


def _parse_decision(result: Mapping[str, Any]) -> RoomDecision:
    text = _decision_response_text(result)
    if not text:
        raise ValueError("room decision response must be non-empty")
    parsed = _parse_json_object(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("room decision response root must be an object")
    reason = _first_non_blank(parsed.get("reason"))
    raw_ids = parsed.get("target_member_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("room decision response must include target_member_ids list")
    selected: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        member_id = _first_non_blank(raw_id)
        if not member_id or member_id in seen:
            continue
        selected.append(member_id)
        seen.add(member_id)
    return RoomDecision(
        target_member_ids=selected,
        reason=reason,
    )


def _parse_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    candidates = [0] if stripped.startswith("{") else []
    candidates.extend(index for index, char in enumerate(stripped) if char == "{" and index not in candidates)
    last_error: json.JSONDecodeError | None = None
    for index in candidates:
        try:
            parsed, _ = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, Mapping):
            return parsed
    if last_error is not None:
        raise ValueError("room decision response must contain a JSON object") from last_error
    raise ValueError("room decision response must contain a JSON object")


def _decision_response_text(result: Mapping[str, Any]) -> str:
    result_payload = result.get("result")
    if isinstance(result_payload, Mapping) and result_payload.get("success") is False:
        reason = (
            result_payload.get("error")
            or result_payload.get("reason")
            or "room decision LLM result returned success=false"
        )
        raise RuntimeError(str(reason))

    nested_result = result_payload if isinstance(result_payload, Mapping) else {}
    choices = result.get("choices") or nested_result.get("choices")
    choice_text = ""
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                choice_text = _first_non_blank(message.get("content"))

    return _first_non_blank(
        result.get("response"),
        result.get("content"),
        result.get("text"),
        nested_result.get("response"),
        nested_result.get("content"),
        nested_result.get("text"),
        choice_text,
    )


def _first_non_blank(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


__all__ = ["RoomDecision", "RoomDecisionService"]
