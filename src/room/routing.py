from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RoomSendPolicy:
    target_member_ids: list[str] | None
    use_llm_decision: bool


def resolve_room_send_policy(
    *,
    target_member_ids: Iterable[str] | None,
) -> RoomSendPolicy:
    requested = [str(member_id).strip() for member_id in (target_member_ids or []) if str(member_id).strip()]
    if requested:
        return RoomSendPolicy(target_member_ids=requested, use_llm_decision=False)
    return RoomSendPolicy(target_member_ids=None, use_llm_decision=True)


__all__ = ["RoomSendPolicy", "resolve_room_send_policy"]
