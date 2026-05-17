# 注册新的 Room 平台适配器

Room 平台适配器把单个平台的成员配置、入站匹配和出站投递意图集中放在一个文件里。新增平台放在：

```text
src/room/platforms/adapters/<platform>.py
```

当前内置平台：

- `qq.py`：QQ 群投递，优先使用 NapCat/OneBot 适配器 API，失败后回退到 SDK stream。
- `discord.py`：Discord 频道投递，优先使用 Discord 适配器 API，失败后回退到 SDK stream。
- `maid.py`：Minecraft 女仆成员投递。它只构造 `maid.message.in` 投递意图，真正发送委托给 `maibot-maid-adapter.maid_message`；本 room 插件不维护 MaidBridge WebSocket 状态。

## 适配器契约

实现 `RoomPlatformAdapter` 协议：

```python
from typing import Any, Mapping

from ..base import OutboundIntent
from ..common import require_id


class ExamplePlatformAdapter:
    key = "example"
    label = "示例平台"
    delivery = "sdk"

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        del default_server_id
        return {"channel_id": require_id(raw_member.get("channel_id"), "channel_id")}

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        return f"example:{endpoint['channel_id']}"

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        return f"示例平台:{endpoint['channel_id']}"

    def host_match_score(self, platform: str, endpoint: Mapping[str, Any], route_ids: set[str]) -> int:
        if platform != self.key:
            return 0
        return 100 if str(endpoint["channel_id"]) in route_ids else 0

    def build_outbound_intent(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        text: str,
        source_member_id: str,
    ) -> OutboundIntent:
        channel_id = require_id(endpoint.get("channel_id"), "channel_id")
        frame = {
            "type": "example.channel.message",
            "endpoint": {"channel_id": channel_id},
            "payload": {"room_id": room_id, "text": text, "source_member_id": source_member_id},
        }
        return OutboundIntent(delivery="sdk", frame=frame, sdk_streams=(("example", channel_id),))
```

`parse_endpoint` 负责校验单个 `config.toml` room member。优先使用 `require_id`、`optional_id` 和 `reject_present`，让错误配置尽早失败。

`default_member_id` 必须返回稳定 ID，例如 `discord:<channel_id>` 或 `qq:<group_id>`。不要把临时消息 ID 放进去。

`host_match_score` 只负责判断入站 host 消息是否属于某个已配置 endpoint。返回 `0` 表示不匹配，分数越高优先级越高。这里不要修改消息。

`build_outbound_intent` 只描述 room 消息应该如何投递，不能直接发送消息。

## 投递类型

`delivery = "sdk"` 用于 MaiBot SDK 已经能触达的平台。`OutboundIntent` 可以包含：

- `frame`：规范化调试快照。
- `sdk_streams`：SDK 兜底 stream 查找参数，例如 `("discord", channel_id)`。
- `adapter_apis`：优先尝试的直发适配器 API 候选。

`delivery = "bridge"` 目前保留给女仆 room 成员。room 插件仍把它视为桥接目标，但实际投递会通过 `adapter_apis` 委托给 `maibot-maid-adapter.maid_message`。不要在这个插件里再加第二套 MaidBridge WebSocket runtime。

## 注册入口

导入适配器，并把实例加入 `src/room/platforms/adapters/__init__.py` 的 `BUILTIN_PLATFORM_ADAPTERS`：

```python
from .example import ExamplePlatformAdapter

BUILTIN_PLATFORM_ADAPTERS = (
    QQPlatformAdapter(),
    DiscordPlatformAdapter(),
    MaidPlatformAdapter(),
    ExamplePlatformAdapter(),
)
```

`src/room/platforms/__init__.py` 会在 import 时注册内置平台，业务代码应通过 registry 取适配器，不要直接 import 具体适配器。
