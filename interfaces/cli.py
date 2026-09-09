"""
interfaces/cli.py — консольный батч-прогон фактчека.
Version: 1.1.0
Description: логгер + запуск FactcheckOrchestrator с реестром инструментов.
"""
import logging
import sys

from openai import OpenAI

from agent.core.tools.registry import ToolRegistry
from agent.orchestrator import FactcheckOrchestrator
from config.settings import get_settings


def setup_logger(log_file: str):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "primp", "rquest", "cookie_store", "duckduckgo_search"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_cli():
    settings = get_settings()
    setup_logger(settings.log_file)
    provider = settings.provider

    print("=" * 60)
    print(f"🔎 Factcheck Agent v{settings.system_version}")
    print(f"   Provider: {provider.display_name}")
    print(f"   Чекеры:   {settings.checker_model_list}")
    print(f"   Арбитр:   {settings.arbiter}")
    print(f"   Input:    {settings.input_dir}/  →  Output: {settings.output_dir}/")
    print(f"   MCP:      🚧 зарезервирован (скрапинг позже)")
    print("=" * 60)

    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    registry = ToolRegistry(openai_client=client)      # mcp_client=None — резерв
    orchestrator = FactcheckOrchestrator(
        client=client, settings=settings, registry=registry,
    )

    try:
        orchestrator.run_batch()
        print(f"\n✅ Отчёты сохранены в {settings.output_dir}/ (check.md, check_N_*.md)")
        print(orchestrator.usage.summary())
    except FileNotFoundError as e:
        print(f"❌ {e}. Положи материалы в {settings.input_dir}/")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        logging.getLogger("cli").exception("Critical error")


if __name__ == "__main__":
    run_cli()