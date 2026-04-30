from typing import Any, Mapping

from ..base import OutboundIntent
from ..common import base_payload, coerce_id, reject_present, require_id


class DiscordPlatformAdapter:
    """Discord channel room 平台适配器"""

    key = "discord"
    label = "Discord"
    delivery = "sdk"

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        """解析 Discord room 成员配置。

        Args:
            raw_member: 单个 Discord member 配置。
            default_server_id: room 默认 server_id，Discord 平台不使用。

        Returns:
            dict[str, Any]: 只包含 channel_id 的 endpoint。
        """
        del default_server_id
        reject_present(raw_member, "guild_id")
        reject_present(raw_member, "thread_id")
        return {"channel_id": require_id(raw_member.get("channel_id"), "channel_id")}

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        return f"discord:{endpoint['channel_id']}"

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        return f"Discord:{endpoint['channel_id']}"

    def host_match_score(self, platform: str, endpoint: Mapping[str, Any], route_ids: set[str]) -> int:
        if platform != self.key:
            return 0
        channel_id = coerce_id(endpoint.get("channel_id"))
        return 100 if channel_id in route_ids else 0

    def build_outbound_intent(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        text: str,
        source_member_id: str,
    ) -> OutboundIntent:
        """构造 Discord 跨频道 room 投递意图。

        Args:
            room_id: 来源 room ID。
            endpoint: 目标 Discord endpoint。
            text: 要发送的文本。
            source_member_id: 来源成员 ID。

        Returns:
            OutboundIntent: 发送层可用的 SDK/API 投递意图。
        """
        channel_id = require_id(endpoint.get("channel_id"), "channel_id")
        frame = {
            "type": "discord.channel.message",
            "resolver": "discord.channel",
            "endpoint": {"channel_id": channel_id},
            "channel_id": channel_id,
            "payload": base_payload(room_id=room_id, text=text, source_member_id=source_member_id),
        }
        return OutboundIntent(
            delivery="sdk",
            frame=frame,
            sdk_streams=(("discord", channel_id),),
            # 跨频道 room 转发不能依赖“目标频道已经存在 MaiBot 聊天流”，所以优先走 Discord 适配器直发 API。
            adapter_apis=(
                {
                    "api_name": "adapter.discord.channel.send_message",
                    "version": "1",
                    "kwargs": {
                        "channel_id": channel_id,
                        "text": text,
                    },
                },
            ),
        )
