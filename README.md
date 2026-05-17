# maibot-bridge-adapter

MaiBot 桥接房间插件。它负责跨平台 room 聚合、room hook、room 路由 API、room 路由 prompt，以及 room 成员投递规划。

MaidBridge WebSocket 运行时、TouhouLittleMaid 女仆 agent 接管、女仆 API 和 Minecraft 聊天网关不在本插件内维护；这些能力属于 `maibot-maid-adapter`。

## 配置

```toml
[plugin]
enabled = true
config_version = "0.1.0"

[room]
enable_room_gate = false
rooms = []
request_timeout_ms = 30000
room_decision_max_context_messages = 20
room_decision_model = "replyer"
room_decision_temperature = 0.1
room_decision_max_tokens = 256
```

`rooms` 不建议在 WebUI 里编辑。需要配置房间时，把 `room_config.template.toml` 里的 `[room]` 示例复制到 `config.toml` 后修改。

## 女仆成员

`platform = "maid"` 的 room 成员仍然是合法目标。实际投递会委托给 `maibot-maid-adapter.maid_message` 公开 API；本插件不会再保存 MaidBridge WebSocket 连接状态，也不会重复实现女仆运行时。

## 公开 API

- `room_status`：查看已配置 room 的运行状态。
- `room_members`：查看指定 room 的成员。
- `room_ingest`：把结构化消息写入 room 缓冲。
- `room_send`：进行 room 路由并投递消息。
