# maibot-bridge-adapter

MaiBot 侧的 MaidBridge 适配器，同时提供可选的跨端 Room 能力。

它不是 Minecraft mod 本体。Java 侧 MaidBridge mod 负责接入 TouhouLittleMaid 和 Minecraft；这个插件负责连接 Java MaidBridge WebSocket 服务，把女仆回合交给 MaiBot 处理，并在需要时把 QQ、Discord、Maid 等不同来源聚合到同一个逻辑 room。

## 工作模式

### 1.启用跨端 Room

Room 用于把多个平台成员放进同一个逻辑会话，例如 QQ 群、Discord 频道和 Minecraft 女仆共享上下文。

只有配置了 `[room].rooms` 后，room gate 才会命中来源消息。未命中 room 的普通 QQ/Discord 消息继续走 MaiBot 原生流程。

### 2.接入女仆

即作为女仆mod的适配器存活

只想要“MC 内消息 -> 女仆 -> MaidBridge WS -> MaiBot -> MaidBridge WS -> 女仆回写”时，不需要配置任何 room。

保持 `[room].rooms = []` 即可。插件仍会作为 MaidBridge adapter 存活：启动 WebSocket client、发送 `bridge.client.hello`、接收 `maid.agent.turn.request`，并把 MaiBot 生成的 `maid.agent.turn.result` 发回 Java mod。

## 配置详情

默认配置保持插件关闭，避免 MaiBot 启动时反复连接未启动的 Minecraft 服务端：

```toml
[plugin]
enabled = false
config_version = "0.1.0"

[room]
enable_room_gate = true
rooms = []

[maid_agent]
enable_maid_agent_turns = true
default_maid_uuid = ""
maid_channel_name = "maid"
maid_channel_id = "1593201413"

[connection]
server_uri = "ws://127.0.0.1:8765/maidbridge"
access_token = ""
```

启用插件时，把 `[plugin].enabled` 改成 `true`。只接入女仆时，`rooms` 继续留空。

## 配置说明

### `[room]`

- `enable_room_gate`: 是否启用跨端 room hook。
- `rooms`: 逻辑 room 列表。WebUI 不编辑复杂嵌套列表，直接写 `config.toml`。

Room 模板见：

```text
MaiBot/plugins/maibot-bridge-adapter/room_config.template.toml
```

### `[maid_agent]`

- `enable_maid_agent_turns`: 是否处理 Java 发来的 `maid.agent.turn.request`。
- `default_maid_uuid`: 调用 `maid_query` / `maid_call` 未传女仆 ID 时使用的默认 UUID。
- `maid_channel_name`: 女仆端在 prompt 或 room 上下文中显示的频道名。
- `maid_channel_id`: 女仆端在 prompt 或 room 上下文中使用的虚拟频道 ID。

### `[connection]`

- `server_uri`: Java MaidBridge WebSocket 服务端地址。
- `access_token`: 可选鉴权 token。Java 侧未启用鉴权时留空。

插件固定作为 WebSocket client 连接 Java mod。`client_roles`、`subscriptions`、请求超时和最大 frame 大小是内部默认值，不需要写进 `config.toml`。

## Room 成员平台

每个 room member 用 `platform` 指定平台适配器：

- `qq`: QQ 群，`channel_id` 填 QQ 群号。
- `discord`: Discord 频道，`channel_id` 填 Discord 频道 ID。
- `maid`: Minecraft 女仆，`channel_id` 填女仆频道，还需要 `maid_uuid` 或 `maid_entity_id`。

平台逻辑集中在 `src/room/platforms/`。新增平台时注册一个 `RoomPlatformAdapter`，不要把平台判断散落到 runtime、delivery 或 hook 里。详细说明见：

```text
docs/register-platform-adapter.md
```

## 主要 API

- `status`: 查看连接状态、room 数量和 pending 请求数量。
- `pending_requests`: 查看等待 Java ACK/NACK 的请求。
- `maid_query`: 发送 MaidBridge 查询帧。
- `maid_call`: 发送 MaidBridge 调用帧。
- `registry_snapshot` / `registry_list` / `registry_get` / `registry_search`: 查询 Java 侧同步来的 registry。
- `endpoints`: 查看已注册的 MaidBridge endpoint。
- `room_status` / `room_members` / `room_ingest` / `room_send`: Room 调试和手动投递 API。

## 数据流

女仆外部接管：

```text
Minecraft/TLM -> Java MaidBridge -> maid.agent.turn.request -> MaiBot
MaiBot -> maid.agent.turn.result -> Java MaidBridge -> 女仆气泡/历史/动作
```

跨端 Room：

```text
QQ/Discord/Maid 入站消息 -> room gate 归一化 -> MaiBot 原生处理
MaiBot 出站消息 -> room 路由 -> QQ/Discord SDK 或 MaidBridge WebSocket
```

## 开源协议

本项目采用 [GPL-v3.0](LICENSE) 协议开源
