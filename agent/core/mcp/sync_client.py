"""
SyncMCPClient — 🚧 РЕЗЕРВ под MCP-скрапинг источников (подключим позже).
Сейчас заглушка: проект полностью работоспособен без MCP.
"""
import logging

logger = logging.getLogger("agent.mcp")


class SyncMCPClient:
    def __init__(self, url: str = "", enabled: bool = False):
        self.url = url
        self.enabled = enabled

    def initialize(self) -> bool:
        if not self.enabled:
            logger.info("🚧 MCP выключен (mcp_enabled=false) — резерв под скрапинг")
        return False

    def tool_names(self) -> list:
        return []

    def tools_catalog_markdown(self) -> str:
        return "(MCP-инструменты не подключены — зарезервировано для скрапинга источников)"

    def call_tool(self, name: str, args: dict):
        raise NotImplementedError("MCP-скрапинг будет подключён в следующей версии")