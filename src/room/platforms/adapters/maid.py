from typing import Any, Mapping

from ....constants import CLIENT_TO_JAVA
from ..base import OutboundIntent
from ..common import optional_id, reject_present, require_id


class MaidPlatformAdapter:
    """Minecraft 女仆 room 平台适配器"""

    key = "maid"
    label = "Maid"
    delivery = "bridge"

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        """解析女仆 room 成员配置。

        Args:
            raw_member: 单个 maid member 配置。
            default_server_id: room 默认 server_id，女仆平台不使用。

        Returns:
            dict[str, Any]: MaidBridge 可识别的 endpoint。
        """
        del default_server_id
        reject_present(raw_member, "server_id")
        channel_id = require_id(raw_member.get("channel_id"), "channel_id")
        maid_uuid = optional_id(raw_member.get("maid_uuid"), "maid_uuid")
        maid_entity_id = optional_id(raw_member.get("maid_entity_id"), "maid_entity_id")
        mode = optional_id(raw_member.get("mode"), "mode") or "maid_message"
        if mode != "maid_message":
            raise ValueError("maid room members only support mode=maid_message")
        endpoint = {"mode": mode, "channel_id": channel_id}
        if maid_uuid:
            endpoint["maid_uuid"] = maid_uuid
        if maid_entity_id:
            endpoint["maid_entity_id"] = maid_entity_id
        return endpoint

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        return f"maid:{endpoint['channel_id']}"

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        del endpoint
        return "maid"

    def host_match_score(self, platform: str, endpoint: Mapping[str, Any], route_ids: set[str]) -> int:
        del platform, endpoint, route_ids
        return 0

    def build_outbound_intent(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        text: str,
        source_member_id: str,
    ) -> OutboundIntent:
        """构造发往 Java MaidBridge 的女仆消息。

        Args:
            room_id: 来源 room ID。
            endpoint: 目标女仆 endpoint。
            text: 要交给女仆的文本。
            source_member_id: 来源成员 ID。

        Returns:
            OutboundIntent: delivery=bridge 的出站意图。
        """
        maid_uuid = optional_id(endpoint.get("maid_uuid"), "maid_uuid")
        maid_entity_id = optional_id(endpoint.get("maid_entity_id"), "maid_entity_id")
        if not maid_uuid and not maid_entity_id:
            raise ValueError("maid_uuid or maid_entity_id must be configured for maid room target")
        channel_id = require_id(endpoint.get("channel_id"), "channel_id")
        endpoint_data = {
            "endpoint_id": f"maid:{channel_id}",
            "channel_id": channel_id,
            "mode": "maid_message",
        }
        if maid_uuid:
            endpoint_data["maid_uuid"] = maid_uuid
        if maid_entity_id:
            endpoint_data["maid_entity_id"] = maid_entity_id
        # 女仆投递必须保留实体定位信息，否则 Java 侧无法把 room 消息交给具体女仆。
        frame = {
            "type": "maid.message.in",
            "resolver": "maidbridge.maid",
            "endpoint": endpoint_data,
            "direction": CLIENT_TO_JAVA,
            "endpoint_id": endpoint_data["endpoint_id"],
            "maid_uuid": maid_uuid,
            "maid_entity_id": maid_entity_id,
            "payload": {
                "text": text,
                "client_info": {
                    "room_id": room_id,
                    "source_member_id": source_member_id,
                    "mode": "maid_message",
                },
                "maid": {
                    "uuid": maid_uuid,
                    "entity_id": maid_entity_id,
                    "channel_id": channel_id,
                },
            },
        }
        return OutboundIntent(delivery="bridge", frame=frame)
