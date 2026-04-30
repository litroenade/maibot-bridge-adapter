from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .common import RoomDeliveryKind


@dataclass(frozen=True)
class OutboundIntent:
    """平台适配器的出站结果。

    room runtime 只关心“要发给谁”；发送层只消费 intent，避免 QQ/Discord/Maid
    的投递细节继续散落在 runtime、outbound 和 delivery 多处。
    """

    delivery: RoomDeliveryKind
    frame: dict[str, Any]
    sdk_streams: tuple[tuple[str, str], ...] = ()
    adapter_apis: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery": self.delivery,
            "frame": dict(self.frame),
            "sdk_streams": [
                {"platform": platform, "group_id": group_id}
                for platform, group_id in self.sdk_streams
            ],
            "adapter_apis": [
                {
                    "api_name": str(candidate.get("api_name") or ""),
                    "version": str(candidate.get("version") or ""),
                    "kwargs": dict(candidate.get("kwargs") or {}),
                }
                for candidate in self.adapter_apis
            ],
        }


class RoomPlatformAdapter(Protocol):
    """room 平台适配器协议"""

    key: str
    label: str
    delivery: RoomDeliveryKind

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        """解析配置文件里的 room member。

        Args:
            raw_member: 单个 room member 的原始配置。
            default_server_id: room 级默认 server_id。

        Returns:
            dict[str, Any]: 统一后的 endpoint 数据。
        """
        raise NotImplementedError

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        """生成未显式配置 member_id 时使用的稳定 ID"""
        raise NotImplementedError

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        """生成未显式配置 display_name 时使用的显示名"""
        raise NotImplementedError

    def host_match_score(self, platform: str, endpoint: Mapping[str, Any], route_ids: set[str]) -> int:
        """计算入站消息是否属于该 endpoint。

        Returns:
            int: 0 表示不匹配，分数越高优先级越高。
        """
        raise NotImplementedError

    def build_outbound_intent(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        text: str,
        source_member_id: str,
    ) -> OutboundIntent:
        """把 room 消息转换成平台投递意图。

        Args:
            room_id: 来源 room ID。
            endpoint: 目标成员 endpoint。
            text: 要投递的文本。
            source_member_id: 来源成员 ID。

        Returns:
            OutboundIntent: 发送层可直接消费的投递意图。
        """
        raise NotImplementedError
