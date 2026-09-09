"""
Реестр инструментов агента.
Version: 1.1.0
Description: Локальные + веб-инструменты фактчека. MCP-слот зарезервирован под скрапинг.
"""
from agent.core.mcp.sync_client import SyncMCPClient
from agent.core.tools.agent_tools import (
    bash_execute, code_execute, collect_tools, create_tool_router,
    file_read, file_write, filter_tools_for_skill, load_skill,
    web_read, web_search,
)


class ToolRegistry:
    def __init__(self, openai_client, mcp_client: SyncMCPClient = None):
        self.openai_client = openai_client
        self.mcp_client = mcp_client          # 🚧 резерв: скрапинг источников позже
        self._init_tools()

    def _init_tools(self):
        self.all_tools = [
            load_skill, bash_execute, file_read, file_write,
            web_search, web_read, code_execute,
        ]
        # 🚧 РЕЗЕРВ (не раскомментировать до подключения MCP):
        # self.all_tools.extend(build_mcp_scrape_tools(self.mcp_client))
        self.schemas = collect_tools(*self.all_tools)
        self.router = create_tool_router(*self.all_tools)

    def get_tools_for_skill(self, skill_name: str):
        filtered = filter_tools_for_skill(self.all_tools, skill_name)
        return collect_tools(*filtered), create_tool_router(*filtered)