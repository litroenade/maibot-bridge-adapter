from copy import deepcopy
from typing import Any, ClassVar

from maibot_sdk import Field, PluginConfigBase

SUPPORTED_CONFIG_VERSION = "0.1.0"
DEFAULT_SERVER_ID = "minecraft-local"
DEFAULT_JAVA_SERVER_URI = "ws://127.0.0.1:8765/maidbridge"
DEFAULT_CLIENT_ROLES = ["agent", "control", "diagnostics"]
DEFAULT_SUBSCRIPTIONS = [
    "maid.agent.turn.request",
    "maid.message.out",
    "maid.api.registry.*",
    "bridge.server.hello",
    "maidbridge.server.*",
]
DEFAULT_GATEWAY_MAX_HOPS = 8
DEFAULT_REQUEST_TIMEOUT_MS = 30000
DEFAULT_MAX_MESSAGE_BYTES = 32768
DEFAULT_MAID_AGENT_TASK = "replyer"
DEFAULT_ROOM_DECISION_TASK = "replyer"
DEFAULT_MAID_AGENT_TEMPERATURE = 0.3
DEFAULT_MAID_AGENT_MAX_TOKENS = 1200
DEFAULT_MAID_AGENT_HISTORY_POLICY = "append"
ROOM_TEMPLATE_PATH = "MaiBot/plugins/maibot-bridge-adapter/room_config.template.toml"
ROOM_TEMPLATE_PREVIEW = """# 复制这一段 [room] 到 config.toml，然后按需自建 rooms/members。
# 只做“女仆 -> 外部 agent -> 女仆回写”时，可以不配置 room。
# room 只用于 QQ/Discord/Maid 多成员聚合。
# room 出站路由固定由 MaiBot 生成出站消息后再由 LLM 选择目标成员，不再提供 strategy 配置项。
# 成员平台：
# - qq: QQ 群，channel_id 填 QQ 群号。
# - discord: Discord 频道，channel_id 填 Discord 频道 ID。
# - maid: Minecraft 女仆端，channel_id 填女仆频道；还需要 maid_uuid 或 maid_entity_id 定位目标女仆实体。
[room]
enable_room_gate = true
rooms = [
    { id = "main-bridge", name = "Main Bridge", members = [
        { platform = "qq", channel_id = "123456789" },
        { platform = "discord", channel_id = "111111111111111111" },
        { platform = "maid", channel_id = "1593201413", maid_uuid = "00000000-0000-0000-0000-000000000000" },
    ] },
]
"""


class MaidBridgePluginOptions(PluginConfigBase):
    """Runner 必需的插件配置节。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_icon__: ClassVar[str] = "package"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="是否启用 maibot-bridge-adapter。",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后插件不启动 WebSocket，也不会处理 MaidBridge 或跨端 room。",
            "order": 0,
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置结构版本。",
        json_schema_extra={"disabled": True, "hidden": True, "label": "配置版本", "order": 99},
    )


class MaidBridgeConnectionConfig(PluginConfigBase):
    """连接 Java MaidBridge WebSocket 服务端的配置。"""

    __ui_label__: ClassVar[str] = "女仆连接"
    __ui_icon__: ClassVar[str] = "plug"
    __ui_order__: ClassVar[int] = 4

    server_uri: str = Field(
        default=DEFAULT_JAVA_SERVER_URI,
        description="Java MaidBridge WebSocket 服务端地址。",
        json_schema_extra={
            "label": "Java MaidBridge 地址",
            "order": 0,
            "placeholder": DEFAULT_JAVA_SERVER_URI,
            "hint": "Java mod 现在作为 WebSocket 服务端，插件只负责连接这个地址。",
        },
    )
    access_token: str = Field(
        default="",
        description="MaidBridge 握手共享 token，未启用鉴权时可留空。",
        json_schema_extra={
            "label": "访问 Token",
            "input_type": "password",
            "order": 1,
            "placeholder": "可留空",
        },
    )
    max_message_bytes: int = Field(
        default=DEFAULT_MAX_MESSAGE_BYTES,
        ge=1024,
        description="单个 WebSocket frame 最大字节数。",
        json_schema_extra={"hidden": True, "label": "最大消息字节数", "order": 2},
    )
    request_timeout_ms: int = Field(
        default=DEFAULT_REQUEST_TIMEOUT_MS,
        ge=1000,
        description="等待 Java ACK/NACK 的超时时间，单位毫秒。",
        json_schema_extra={"hidden": True, "label": "请求超时毫秒", "order": 3},
    )


class MaidBridgeRoomConfig(PluginConfigBase):
    """跨端 room 的运行配置；复杂成员列表直接写入 config.toml。"""

    __ui_label__: ClassVar[str] = "跨端 Room"
    __ui_icon__: ClassVar[str] = "messages-square"
    __ui_order__: ClassVar[int] = 1

    enable_room_gate: bool = Field(
        default=True,
        description="是否启用跨端 room；命中源群聊后归一化为 bridge room 会话并继续 MaiBot 原生链路，出站时再按 room 决策路由。",
        json_schema_extra={
            "label": "启用跨端 Room",
            "hint": f"成员列表在 config.toml 的 [room].rooms 下生效；复制模板见 {ROOM_TEMPLATE_PATH}。",
            "order": 0,
        },
    )
    rooms: list[dict[str, Any]] = Field(
        default_factory=list,
        description="跨端逻辑 room 定义。WebUI 不编辑嵌套数组，直接在 config.toml 的 [room].rooms 写列表。",
        json_schema_extra={
            "hidden": True,
            "label": "Room 定义",
            "hint": f"复制 {ROOM_TEMPLATE_PATH} 中的 [room] 示例到 config.toml。",
            "order": 1,
        },
    )


class MaidBridgeMaidAgentConfig(PluginConfigBase):
    """女仆整轮外部接管只需要开关、默认 UUID 和频道命名。"""

    __ui_label__: ClassVar[str] = "女仆接入"
    __ui_icon__: ClassVar[str] = "bot"
    __ui_order__: ClassVar[int] = 3

    enable_maid_agent_turns: bool = Field(
        default=True,
        description="是否处理 MaidBridge 发来的 maid.agent.turn.request。",
        json_schema_extra={
            "label": "启用女仆接入",
            "hint": "开启后由 MaiBot 接管 MaidBridge mixin 出来的整轮女仆思考。",
            "order": 0,
        },
    )
    default_maid_uuid: str = Field(
        default="",
        description="默认女仆 UUID。调用 maid_query/maid_call 未显式传 maid_uuid 时会使用此值。",
        json_schema_extra={"label": "女仆 UUID", "order": 1, "placeholder": "可留空"},
    )
    maid_channel_name: str = Field(
        default="maid",
        description="女仆端在跨端 room 或外部思考 prompt 中显示的频道名称。",
        json_schema_extra={"label": "女仆频道名", "order": 2, "placeholder": "maid"},
    )
    maid_channel_id: str = Field(
        default="1593201413",
        description="女仆端在外部 agent prompt 中使用的虚拟频道 ID。",
        json_schema_extra={"label": "女仆频道 ID", "order": 3, "placeholder": "1593201413"},
    )


class MaidBridgePluginSettings(PluginConfigBase):
    plugin: MaidBridgePluginOptions = Field(default_factory=MaidBridgePluginOptions)
    room: MaidBridgeRoomConfig = Field(default_factory=MaidBridgeRoomConfig)
    maid_agent: MaidBridgeMaidAgentConfig = Field(default_factory=MaidBridgeMaidAgentConfig)
    connection: MaidBridgeConnectionConfig = Field(default_factory=MaidBridgeConnectionConfig)

    @property
    def config_version(self) -> str:
        return self.plugin.config_version

    @property
    def enabled(self) -> bool:
        return self.plugin.enabled

    @property
    def server_id(self) -> str:
        return DEFAULT_SERVER_ID

    @property
    def websocket_role(self) -> str:
        return "client"

    @property
    def websocket_url(self) -> str:
        return self.connection.server_uri.strip() or DEFAULT_JAVA_SERVER_URI

    @property
    def access_token(self) -> str:
        return self.connection.access_token

    @property
    def max_message_bytes(self) -> int:
        return self.connection.max_message_bytes

    @property
    def request_timeout_ms(self) -> int:
        return self.connection.request_timeout_ms

    @property
    def client_roles(self) -> list[str]:
        return list(DEFAULT_CLIENT_ROLES)

    @property
    def subscriptions(self) -> list[str]:
        return list(DEFAULT_SUBSCRIPTIONS)

    @property
    def gateway_max_hops(self) -> int:
        return DEFAULT_GATEWAY_MAX_HOPS

    @property
    def enable_message_gateway(self) -> bool:
        return False

    @property
    def enable_room_gate(self) -> bool:
        return self.room.enable_room_gate

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return deepcopy(self.room.rooms)

    @property
    def enable_maid_agent_turns(self) -> bool:
        return self.maid_agent.enable_maid_agent_turns

    @property
    def default_maid_uuid(self) -> str:
        return self.maid_agent.default_maid_uuid.strip()

    @property
    def maid_channel_name(self) -> str:
        return self.maid_agent.maid_channel_name.strip() or "maid"

    @property
    def maid_channel_id(self) -> str:
        return self.maid_agent.maid_channel_id.strip() or "1593201413"

    @property
    def maid_agent_model(self) -> str:
        return DEFAULT_MAID_AGENT_TASK

    @property
    def maid_agent_temperature(self) -> float:
        return DEFAULT_MAID_AGENT_TEMPERATURE

    @property
    def maid_agent_max_tokens(self) -> int:
        return DEFAULT_MAID_AGENT_MAX_TOKENS

    @property
    def maid_agent_history_policy(self) -> str:
        return DEFAULT_MAID_AGENT_HISTORY_POLICY

    @property
    def enable_room_llm_decision(self) -> bool:
        return True

    @property
    def room_decision_model(self) -> str:
        return DEFAULT_ROOM_DECISION_TASK

    @property
    def room_decision_temperature(self) -> float:
        return 0.1

    @property
    def room_decision_max_tokens(self) -> int:
        return 256

    @property
    def room_decision_max_context_messages(self) -> int:
        return 20


def build_room_guide_schema() -> dict[str, Any]:
    return {
        "name": "room_guide",
        "title": "跨端 Room 模板",
        "description": "只读复制模板；实际生效内容仍写在 config.toml 的 [room] 下。",
        "icon": "file-code-2",
        "collapsed": False,
        "order": 2,
        "fields": {
            "room_config_template": {
                "name": "room_config_template",
                "type": "string",
                "default": ROOM_TEMPLATE_PREVIEW,
                "description": f"模板文件：{ROOM_TEMPLATE_PATH}",
                "required": False,
                "choices": None,
                "min": None,
                "max": None,
                "step": None,
                "pattern": None,
                "max_length": None,
                "label": "config.toml Room 模板",
                "placeholder": None,
                "hint": f"复制 {ROOM_TEMPLATE_PATH} 中的 [room] 段到 config.toml。",
                "icon": None,
                "hidden": False,
                "disabled": True,
                "order": 0,
                "input_type": None,
                "ui_type": "textarea",
                "rows": 18,
                "group": None,
                "depends_on": None,
                "depends_value": None,
                "item_type": None,
                "item_fields": None,
                "min_items": None,
                "max_items": None,
                "example": None,
            }
        },
    }
