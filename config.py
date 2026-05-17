from copy import deepcopy
from typing import Any, ClassVar

from maibot_sdk import Field, PluginConfigBase

SUPPORTED_CONFIG_VERSION = "0.1.0"
DEFAULT_SERVER_ID = "minecraft-local"
DEFAULT_REQUEST_TIMEOUT_MS = 30000
ROOM_TEMPLATE_PATH = "MaiBot/plugins/maibot-bridge-adapter/room_config.template.toml"
ROOM_TEMPLATE_PREVIEW = """# 复制这一段 [room] 到 config.toml，然后按需调整 rooms/members。
# room 只负责 QQ/Discord/Maid 等多成员聚合，不负责 MaidBridge WebSocket。
# 女仆成员投递会委托给 maibot-maid-adapter 的公开 API。
[room]
enable_room_gate = true
rooms = [
    { id = "main-bridge", name = "主桥接房间", session_platform = "discord", members = [
        { platform = "qq", channel_id = "123456789" },
        { platform = "discord", channel_id = "111111111111111111" },
        { platform = "maid", channel_id = "1593201413", maid_uuid = "00000000-0000-0000-0000-000000000000" },
    ] },
]
"""


class MaidBridgeRoomPluginOptions(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "package"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="是否启用桥接房间插件。",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后仍会注册 room hooks 和 room API，但不会处理桥接房间流量。",
            "order": 0,
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置结构版本。",
        json_schema_extra={"disabled": True, "hidden": True, "label": "配置版本", "order": 99},
    )


class MaidBridgeRoomConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "桥接房间"
    __ui_icon__: ClassVar[str] = "messages-square"
    __ui_order__: ClassVar[int] = 1

    enable_room_gate: bool = Field(
        default=False,
        description="是否把已配置来源聊天归一化为桥接房间会话。",
        json_schema_extra={
            "label": "启用桥接房间",
            "hint": f"在 config.toml 的 [room].rooms 中配置房间。模板文件：{ROOM_TEMPLATE_PATH}。",
            "order": 0,
        },
    )
    rooms: list[dict[str, Any]] = Field(
        default_factory=list,
        description="桥接房间定义。",
        json_schema_extra={
            "hidden": True,
            "label": "房间定义",
            "hint": f"把 {ROOM_TEMPLATE_PATH} 中的 [room] 示例复制到 config.toml 后再修改。",
            "order": 1,
        },
    )
    request_timeout_ms: int = Field(
        default=DEFAULT_REQUEST_TIMEOUT_MS,
        ge=1000,
        description="房间 LLM 路由和外部适配器 API 投递的超时时间。",
        json_schema_extra={"hidden": True, "label": "请求超时毫秒", "order": 2},
    )
    room_decision_max_context_messages: int = Field(
        default=20,
        ge=1,
        le=200,
        description="传给房间路由 prompt 的历史消息数量上限。",
        json_schema_extra={"hidden": True, "label": "决策上下文消息数", "order": 3},
    )
    room_decision_model: str = Field(
        default="replyer",
        description="房间路由决策使用的 LLM 模型别名。",
        json_schema_extra={"hidden": True, "label": "决策模型", "order": 4},
    )
    room_decision_temperature: float = Field(
        default=0.1,
        ge=0,
        le=2,
        description="房间路由决策使用的 temperature。",
        json_schema_extra={"hidden": True, "label": "决策 temperature", "order": 5},
    )
    room_decision_max_tokens: int = Field(
        default=256,
        ge=1,
        description="房间路由决策生成内容的 token 上限。",
        json_schema_extra={"hidden": True, "label": "决策 token 上限", "order": 6},
    )


class MaidBridgeRoomAdapterSettings(PluginConfigBase):
    plugin: MaidBridgeRoomPluginOptions = Field(default_factory=MaidBridgeRoomPluginOptions)
    room: MaidBridgeRoomConfig = Field(default_factory=MaidBridgeRoomConfig)

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
    def request_timeout_ms(self) -> int:
        return self.room.request_timeout_ms

    @property
    def enable_room_gate(self) -> bool:
        return self.room.enable_room_gate

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return deepcopy(self.room.rooms)

    @property
    def room_decision_max_context_messages(self) -> int:
        return self.room.room_decision_max_context_messages

    @property
    def room_decision_model(self) -> str:
        return self.room.room_decision_model

    @property
    def room_decision_temperature(self) -> float:
        return self.room.room_decision_temperature

    @property
    def room_decision_max_tokens(self) -> int:
        return self.room.room_decision_max_tokens


MaidBridgePluginSettings = MaidBridgeRoomAdapterSettings


def build_room_guide_schema() -> dict[str, Any]:
    return {
        "name": "room_guide",
        "title": "桥接房间配置模板",
        "description": "只读的房间配置模板；实际生效配置仍以 config.toml 的 [room] 为准。",
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
                "label": "config.toml 房间模板",
                "placeholder": None,
                "hint": f"把 {ROOM_TEMPLATE_PATH} 中的 [room] 段复制到 config.toml。",
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
