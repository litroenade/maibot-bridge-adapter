import asyncio
from typing import Any, ClassVar

from maibot_sdk import MaiBotPlugin, PluginConfigBase

from .config import MaidBridgeRoomAdapterSettings, build_room_guide_schema
from .src.room.plugin import MaidBridgeRoomPlugin
from .src.room.recorder import RoomSourceRecorder
from .src.room.runtime import RoomRuntime


class MaidBridgeRoomAdapterPlugin(MaidBridgeRoomPlugin, MaiBotPlugin):
    config_model: ClassVar[type[PluginConfigBase] | None] = MaidBridgeRoomAdapterSettings

    def __init__(self) -> None:
        super().__init__()
        self._room_runtime: RoomRuntime | None = None
        self._room_source_recorder: RoomSourceRecorder | None = None
        self._room_dispatch_tasks: set[asyncio.Task[None]] = set()
        self._room_session_context_by_stream_id: dict[str, dict[str, str]] = {}

    async def on_load(self) -> None:
        settings = self._settings()
        if not settings.enabled:
            self.ctx.logger.info("MaidBridge 桥接房间适配器已关闭")
            return
        if settings.enable_room_gate:
            self._room_runtime_instance()
        self.ctx.logger.info("MaidBridge 桥接房间适配器已加载")

    async def on_unload(self) -> None:
        self.ctx.logger.info("正在停止 MaidBridge 桥接房间适配器")
        await self._cancel_room_dispatch_tasks()
        self._room_runtime = None
        self._room_source_recorder = None
        self._room_session_context_by_stream_id.clear()
        self.ctx.logger.info("MaidBridge 桥接房间适配器已停止")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        if scope != "self":
            return
        self.ctx.logger.info(f"正在重载 MaidBridge 桥接房间适配器配置 [version={version}]")
        await self._cancel_room_dispatch_tasks()
        self.set_plugin_config(config_data)
        self._room_runtime = None
        self._room_session_context_by_stream_id.clear()
        settings = self._settings()
        if settings.enabled and settings.enable_room_gate:
            self._room_runtime_instance()

    def get_webui_config_schema(
        self,
        *,
        plugin_id: str = "",
        plugin_name: str = "",
        plugin_version: str = "",
        plugin_description: str = "",
        plugin_author: str = "",
    ) -> dict[str, Any]:
        schema = super().get_webui_config_schema(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            plugin_description=plugin_description,
            plugin_author=plugin_author,
        )
        sections = schema.setdefault("sections", {})
        sections["room_guide"] = build_room_guide_schema()
        schema["sections"] = dict(
            sorted(
                sections.items(),
                key=lambda item: (int(item[1].get("order", 0)) if isinstance(item[1], dict) else 0, item[0]),
            )
        )
        return schema

    def _settings(self) -> MaidBridgeRoomAdapterSettings:
        return self.config if isinstance(self.config, MaidBridgeRoomAdapterSettings) else MaidBridgeRoomAdapterSettings()


MaidBridgeAdapterPlugin = MaidBridgeRoomAdapterPlugin


def create_plugin() -> MaidBridgeRoomAdapterPlugin:
    return MaidBridgeRoomAdapterPlugin()
