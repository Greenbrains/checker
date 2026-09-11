"""
interfaces/cli.py — консольный батч-прогон фактчека.
Version: 2.2.0
Description: логгер + запуск оркестратора по версии пайплайна (v1, v2, v3, v3.1).
"""
import logging
import sys
from openai import OpenAI

from agent.core.tools.registry import ToolRegistry
from agent.orchestrator import FactcheckOrchestrator
from config.settings import get_settings


def setup_logger(log_file: str):
    """Настройка логгера: файл + консоль."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "primp", "rquest", "cookie_store", "duckduckgo_search"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_cli():
    """Запуск CLI с выбором пайплайна из настроек."""
    settings = get_settings()
    setup_logger(settings.log_file)
    
    pipeline = settings.pipeline_version
    
    print(f"\n{'='*10}")
    print(f"🚀 ЗАПУСК Пайплайна: {pipeline.upper()}")
    print(f"{'='*10}")
    
    provider = settings.provider
    print(f"🔎 Factcheck Agent v{settings.system_version}")
    print(f"   Provider: {provider.display_name}")
    print(f"   Чекеры:   {settings.checker_model_list}")
    print(f"   Арбитр:   {settings.arbiter}")
    print(f"{'='*10}")

    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    registry = ToolRegistry(openai_client=client)
    
    if pipeline == "v2":
        try:
            from agent.orchestrator_v2 import FactcheckOrchestratorV2
            orchestrator = FactcheckOrchestratorV2(
                client=client, settings=settings, registry=registry,
            )
        except ImportError as e:
            print(f"❌ Ошибка импорта v2: {e}")
            return
    elif pipeline == "v3":
        try:
            from agent.orchestrator_v3 import FactcheckOrchestratorV3
            orchestrator = FactcheckOrchestratorV3(
                client=client, settings=settings, registry=registry,
            )
        except ImportError as e:
            print(f"❌ Ошибка импорта v3: {e}")
            return
    elif pipeline == "v3.1":
        try:
            from agent.orchestrator_v31 import FactcheckOrchestratorV31
            orchestrator = FactcheckOrchestratorV31()
        except ImportError as e:
            print(f"❌ Ошибка импорта v3.1: {e}")
            return
    else:
        orchestrator = FactcheckOrchestrator(
            client=client, settings=settings, registry=registry,
        )

    try:
        orchestrator.run_batch()
        print(f"\n✅ Отчёты для {pipeline} сохранены в {settings.output_dir}/")
        if hasattr(orchestrator, 'usage'):
            print(orchestrator.usage.summary())
    except FileNotFoundError as e:
        print(f"❌ {e}. Положи материалы в {settings.input_dir}/")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        logging.getLogger("cli").exception("Critical error")


if __name__ == "__main__":
    run_cli()