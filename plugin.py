import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from maibot_sdk import MaiBotPlugin, PluginConfigBase

from .config import MaidBridgePluginSettings, build_room_guide_schema
from .src.maid.plugin import MaidBridgeMaidPlugin
from .src.maid.runtime.runtime_router import RuntimeRouter
from .src.maid.runtime.state import BridgeRuntimeState
from .src.maid.transport import BridgeTransport
from .src.room.plugin import MaidBridgeRoomPlugin
from .src.room.recorder import RoomSourceRecorder
from .src.room.runtime import RoomRuntime


class MaidBridgeAdapterPlugin(MaidBridgeRoomPlugin, MaidBridgeMaidPlugin, MaiBotPlugin):
    config_model: ClassVar[type[PluginConfigBase] | None] = MaidBridgePluginSettings

    def __init__(
        self,
        *,
        transport_factory: Callable[[MaidBridgePluginSettings], BridgeTransport] | None = None,
    ) -> None:
        super().__init__()
        self._state = BridgeRuntimeState()
        self._transport_factory = transport_factory
        self._transport: BridgeTransport | None = None
        self._router: RuntimeRouter | None = None
        self._room_runtime: RoomRuntime | None = None
        self._room_source_recorder: RoomSourceRecorder | None = None
        self._room_dispatch_tasks: set[asyncio.Task[None]] = set()
        self._room_session_context_by_stream_id: dict[str, dict[str, str]] = {}

    async def on_load(self) -> None:
        settings = self._settings()
        if not settings.enabled:
            self.ctx.logger.info("MaidBridge adapter disabled; runtime will not start")
            await self._publish_gateway_state(ready=False, metadata={"enabled": False})
            return
        await self._start_runtime(settings)

    async def on_unload(self) -> None:
        self.ctx.logger.info("Stopping MaidBridge adapter runtime")
        await self._stop_runtime()
        self._state.mark_disconnected()
        await self._publish_gateway_state(ready=False, metadata={"reason": "plugin_unload"})
        self.ctx.logger.info("MaidBridge adapter runtime stopped")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        if scope != "self":
            return
        self.ctx.logger.info(f"Reloading MaidBridge adapter config [version={version}]")
        await self._stop_runtime()
        self.set_plugin_config(config_data)
        self._room_runtime = None
        self._room_session_context_by_stream_id.clear()
        settings = self._settings()
        if settings.enabled:
            await self._start_runtime(settings, metadata={"config_version": version})
            return
        self._state.mark_disconnected()
        await self._publish_gateway_state(ready=False, metadata={"enabled": False, "config_version": version})
        self.ctx.logger.info(f"MaidBridge adapter disabled after config reload [version={version}]")

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

    def _settings(self) -> MaidBridgePluginSettings:
        return self.config if isinstance(self.config, MaidBridgePluginSettings) else MaidBridgePluginSettings()


def create_plugin() -> MaidBridgeAdapterPlugin:
    return MaidBridgeAdapterPlugin()
