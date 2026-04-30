from typing import Any, Mapping

from ..base import OutboundIntent
from ..common import base_payload, optional_id, reject_present, require_id


class QQPlatformAdapter:
    """QQ 群 room 平台适配器"""

    key = "qq"
    label = "QQ"
    delivery = "sdk"

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        """解析 QQ room 成员配置。

        Args:
            raw_member: 单个 QQ member 配置。
            default_server_id: room 默认 server_id，QQ 平台不使用。

        Returns:
            dict[str, Any]: 标准化后的 QQ endpoint。
        """
        del default_server_id
        reject_present(raw_member, "group_id")
        endpoint = {"channel_id": require_id(raw_member.get("channel_id"), "channel_id")}
        adapter_api = optional_id(raw_member.get("adapter_api"), "adapter_api")
        adapter_api_version = optional_id(raw_member.get("adapter_api_version"), "adapter_api_version")
        if adapter_api:
            endpoint["adapter_api"] = adapter_api
        if adapter_api_version:
            endpoint["adapter_api_version"] = adapter_api_version
        return endpoint

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        return f"qq:{endpoint['channel_id']}"

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        return f"QQ:{endpoint['channel_id']}"

    def host_match_score(self, platform: str, endpoint: Mapping[str, Any], route_ids: set[str]) -> int:
        return 100 if platform == self.key and endpoint.get("channel_id") in route_ids else 0

    def build_outbound_intent(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        text: str,
        source_member_id: str,
    ) -> OutboundIntent:
        """构造 QQ 群 room 投递意图。

        Args:
            room_id: 来源 room ID。
            endpoint: 目标 QQ endpoint。
            text: 要发送的文本。
            source_member_id: 来源成员 ID。

        Returns:
            OutboundIntent: 发送层可用的 SDK/API 投递意图。
        """
        channel_id = require_id(endpoint.get("channel_id"), "channel_id")
        frame = {
            "type": "napcat.group.message",
            "resolver": "napcat.group",
            "endpoint": {"channel_id": channel_id},
            "channel_id": channel_id,
            "payload": base_payload(room_id=room_id, text=text, source_member_id=source_member_id),
        }
        return OutboundIntent(
            delivery="sdk",
            frame=frame,
            sdk_streams=(("qq", channel_id),),
            adapter_apis=tuple(self._adapter_api_candidates(endpoint, text)),
        )

    def _adapter_api_candidates(self, endpoint: Mapping[str, Any], text: str) -> list[dict[str, Any]]:
        """生成 QQ 适配器 API 候选列表。

        Returns:
            list[dict[str, Any]]: 按优先级排列的 adapter API 调用参数。
        """
        channel_id = require_id(endpoint.get("channel_id"), "channel_id")
        message = [{"type": "text", "data": {"text": text}}]
        send_group_msg_params = {
            "group_id": channel_id,
            "message": message,
        }
        adapter_api = optional_id(endpoint.get("adapter_api"), "adapter_api")
        adapter_api_version = optional_id(endpoint.get("adapter_api_version"), "adapter_api_version")
        if adapter_api:
            return [
                {
                    "api_name": adapter_api,
                    "version": adapter_api_version,
                    "kwargs": {"params": send_group_msg_params},
                }
            ]
        # QQ 群聊优先走 NapCat adapter API；SDK stream 在部分接入下查不到，只作为兜底。
        return [
            {
                "api_name": "adapter.napcat.group.send_group_msg",
                "kwargs": {"params": send_group_msg_params},
            },
            {
                "api_name": "adapter.napcat.message.send_msg",
                "kwargs": {
                    "params": {
                        "message_type": "group",
                        "group_id": channel_id,
                        "message": message,
                    }
                },
            },
            {
                "api_name": "adapter.napcat.action.call",
                "kwargs": {
                    "action_name": "send_group_msg",
                    "params": send_group_msg_params,
                },
            },
        ]
