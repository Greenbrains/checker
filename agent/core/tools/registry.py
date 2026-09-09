"""
Реестр инструментов агента.
Version: 2.0.0
Description: Веб-инструменты + code_execute + load_skill фактчек-агента.
MCP-слот зарезервирован под скрапинг.
История: 2.0.0 — удалены bash_execute/file_read/file_write (не используются чекерами),
    filter_tools_for_skill (логика переехала в V2_TOOLSETS оркестратора v2),
    убраны упоминания YandexTools и folder_id.
"""
from agent.core.mcp.sync_client import SyncMCPClient
from agent.core.tools.agent_tools import (
    code_execute,
    collect_tools,
    create_tool_router,
    load_skill,
    web_read,
    web_search,
)


class ToolRegistry:
    """Единственная точка сборки инструментов фактчек-агента."""

    def __init__(self, openai_client, mcp_client: SyncMCPClient = None):
        self.openai_client = openai_client
        self.mcp_client = mcp_client  # 🚧 резерв: скрапинг источников позже
        self._init_tools()

    def _init_tools(self):
        self.all_tools = [
            load_skill,
            web_search,
            web_read,
            code_execute,
        ]
        # 🚧 РЕЗЕРВ (не раскомментировать до подключения MCP-скрапинга):
        # self.all_tools.extend(build_mcp_scrape_tools(self.mcp_client))
        self.schemas = collect_tools(*self.all_tools)
        self.router = create_tool_router(*self.all_tools)

    def get_tools_for_skill(self, skill_name: str):
        """Возвращает (schemas, router) для навыка.

        Пайплайн v1: fact-checking → [web_search, web_read, code_execute]
        Пайплайн v2: фильтрация через V2_TOOLSETS в оркестраторе.
        """
        SKILL_TOOLSETS = {
            "fact-checking": {"web_search", "web_read", "code_execute"},
        }
        allowed = SKILL_TOOLSETS.get(skill_name)
        if allowed is None:
            # неизвестный навык — все инструменты
            filtered = self.all_tools
        else:
            filtered = [fn for fn in self.all_tools
                        if getattr(fn, "_tool_name", None) in allowed]
        return collect_tools(*filtered), create_tool_router(*filtered)
