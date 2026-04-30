# 注册新的 Room 平台适配器

Room 平台适配器把某个平台的配置、入站匹配和出站投递意图收敛到一个类里。新增平台时，优先扩展 adapter，不要把平台判断写进 `RoomRuntime`、`RoomDelivery` 或 hook 流程。

## 文件位置

新增文件放在：

```text
src/room/platforms/adapters/<platform>.py
```

现有示例：

- `discord.py`: Discord 频道，优先走 adapter API，失败时可回退 SDK stream。
- `qq.py`: QQ 群，优先走 NapCat/OneBot API，失败时回退 SDK stream。
- `maid.py`: Minecraft 女仆端，生成 `maid.message.in` frame，交给 MaidBridge WebSocket 投递。

## 实现接口

新平台类需要实现 `RoomPlatformAdapter` 约定的方法：

```python
from typing import Any, Mapping

from ..base import OutboundIntent
from ..common import require_id


class ExamplePlatformAdapter:
    key = "example"
    label = "Example"
    delivery = "sdk"

    def parse_endpoint(self, raw_member: Mapping[str, Any], default_server_id: str) -> dict[str, Any]:
        del default_server_id
        return {"channel_id": require_id(raw_member.get("channel_id"), "channel_id")}

    def default_member_id(self, endpoint: Mapping[str, Any]) -> str:
        return f"example:{endpoint['channel_id']}"

    def default_display_name(self, endpoint: Mapping[str, Any]) -> str:
        return f"Example:{endpoint['channel_id']}"

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

`parse_endpoint` 负责把 `config.toml` 里的 member 配置转成稳定 endpoint。这里应使用 `require_id`、`optional_id`、`reject_present` 做校验，尽早拒绝含糊配置。

`default_member_id` 要返回稳定 ID，例如 `discord:<channel_id>`、`qq:<group_id>`。这个 ID 会被 room 决策、日志和 API 使用，不应包含临时消息 ID。

`host_match_score` 用于入站消息匹配 room member。返回 `0` 表示不匹配，数值越高优先级越高。只做平台端点匹配，不要在这里修改消息。

`build_outbound_intent` 返回 `OutboundIntent`。它描述如何投递，不直接发消息。

## 投递类型

`delivery="sdk"` 适合 QQ、Discord 这类 MaiBot 已有平台。`OutboundIntent` 可以带：

- `frame`: 标准化后的目标帧，用于日志和调试。
- `sdk_streams`: SDK 回退路径，例如 `("discord", channel_id)`。
- `adapter_apis`: 优先调用的外部适配器 API 候选。

`delivery="bridge"` 适合 MaidBridge 这种插件自带 transport 的目标。此时 `RoomDelivery` 会把 `frame` 交给 MaidBridge 发送路径。

## 注册入口

在 `src/room/platforms/adapters/__init__.py` 导入并加入 `BUILTIN_PLATFORM_ADAPTERS`：

```python
from .example import ExamplePlatformAdapter

BUILTIN_PLATFORM_ADAPTERS = (
    QQPlatformAdapter(),
    DiscordPlatformAdapter(),
    MaidPlatformAdapter(),
    ExamplePlatformAdapter(),
)
```

`src/room/platforms/__init__.py` 会在导入时调用 `register_builtin_platforms()`，因此业务代码只通过 registry 访问平台能力。

## 配置示例

平台 member 写在 `[room].rooms[].members`：

```toml
[room]
enable_room_gate = true
rooms = [
    { id = "main-bridge", name = "Main Bridge", members = [
        { platform = "example", channel_id = "123456" },
    ] },
]
```

字段名应尽量保持业务含义稳定。比如群、频道统一用 `channel_id`；特殊字段只在 adapter 内解释。

## 边界约定

如果新增平台需要真实网络发送，优先通过该平台自己的 MaiBot adapter API 暴露直发能力，然后在 `adapter_apis` 里声明调用候选。
