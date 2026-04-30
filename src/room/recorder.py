import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


DeserializeMessage = Callable[[Mapping[str, Any]], Any]
StoreMessage = Callable[[Any], Any]
BanChecker = Callable[[str], tuple[bool, str]]


@dataclass
class RoomSourceRecorder:
    deserialize_message: DeserializeMessage | None = None
    store_message: StoreMessage | None = None
    chat_manager: Any | None = None
    check_ban_words: BanChecker | None = None
    check_ban_regex: BanChecker | None = None

    async def record(self, message: Mapping[str, Any]) -> dict[str, Any]:
        session_message = self._deserialize_message(dict(message))
        text = str(getattr(session_message, "processed_plain_text", "") or "")

        banned, word = self._check_ban_words(text)
        if banned:
            return {"success": False, "skipped": True, "reason": "ban_word", "word": word}
        banned_regex, pattern = self._check_ban_regex(text)
        if banned_regex:
            return {"success": False, "skipped": True, "reason": "ban_regex", "pattern": pattern}

        chat_manager = self._chat_manager()
        chat_manager.register_message(session_message)
        await _maybe_await(
            chat_manager.get_or_create_session(
                platform=_message_platform(session_message),
                user_id=_message_user_id(session_message),
                group_id=_message_group_id(session_message),
                account_id=_message_account_id(session_message),
                scope=_message_scope(session_message),
            )
        )
        self._store_message(session_message)
        return {
            "success": True,
            "skipped": False,
            "session_id": str(getattr(session_message, "session_id", "") or ""),
        }

    def _deserialize_message(self, message: dict[str, Any]) -> Any:
        if self.deserialize_message is not None:
            return self.deserialize_message(message)
        from src.plugin_runtime.hook_payloads import deserialize_session_message

        return deserialize_session_message(message)

    def _store_message(self, session_message: Any) -> None:
        if self.store_message is not None:
            self.store_message(session_message)
            return
        from src.common.utils.utils_message import MessageUtils

        MessageUtils.store_message_to_db(session_message)

    def _chat_manager(self) -> Any:
        if self.chat_manager is not None:
            return self.chat_manager
        from src.chat.message_receive.chat_manager import chat_manager

        return chat_manager

    def _check_ban_words(self, text: str) -> tuple[bool, str]:
        if self.check_ban_words is not None:
            return self.check_ban_words(text)
        from src.common.utils.utils_message import MessageUtils

        return MessageUtils.check_ban_words(text)

    def _check_ban_regex(self, text: str) -> tuple[bool, str]:
        if self.check_ban_regex is not None:
            return self.check_ban_regex(text)
        from src.common.utils.utils_message import MessageUtils

        return MessageUtils.check_ban_regex(text)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _message_platform(message: Any) -> str:
    return str(getattr(message, "platform", "") or "")


def _message_user_id(message: Any) -> str:
    user_info = getattr(getattr(message, "message_info", None), "user_info", None)
    return str(getattr(user_info, "user_id", "") or "")


def _message_group_id(message: Any) -> str | None:
    group_info = getattr(getattr(message, "message_info", None), "group_info", None)
    group_id = str(getattr(group_info, "group_id", "") or "")
    return group_id or None


def _message_account_id(message: Any) -> str | None:
    account_id, _ = _message_route_components(message)
    return account_id


def _message_scope(message: Any) -> str | None:
    _, scope = _message_route_components(message)
    return scope


def _message_route_components(message: Any) -> tuple[str | None, str | None]:
    additional_config = getattr(getattr(message, "message_info", None), "additional_config", None)
    if not isinstance(additional_config, dict):
        return None, None
    account_id = _optional_string(
        additional_config.get("platform_io_account_id")
        or additional_config.get("account_id")
        or additional_config.get("self_id")
    )
    scope = _optional_string(
        additional_config.get("platform_io_scope")
        or additional_config.get("scope")
        or additional_config.get("platform_io_target_group_id")
    )
    return account_id, scope


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


__all__ = ["RoomSourceRecorder"]
