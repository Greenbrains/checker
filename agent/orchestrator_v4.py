"""
agent/orchestrator_v4.py — оркестратор v4 (тонкий, следует графу состояний)
"""
import logging
from pathlib import Path

from openai import OpenAI

from config.settings import get_settings
from agent.core.tools.registry import ToolRegistry
from agent.core.readers.input_reader import load_input_documents
from agent.state import StateGraph, PipelineContext
from agent.cache import SourceCache

logger = logging.getLogger("agent.orchestrator_v4")


class FactcheckOrchestratorV4:
    """Оркестратор v4 с графом состояний."""
    
    def __init__(self):
        self.settings = get_settings()
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Инициализация клиента
        self.client = OpenAI(
            api_key=self.settings.provider.api_key,
            base_url=self.settings.provider.base_url
        )
        
        # Реестр инструментов
        self.registry = ToolRegistry(self.client)
        
        # Граф состояний
        self.graph = StateGraph()
        
        # Кеш источников
        self.source_cache = SourceCache()
        
        logger.info(f"🚀 Orchestrator v4 инициализирован")
        logger.info(f"📁 Output dir: {self.settings.output_dir}")
    
    def run_batch(self) -> str:
        """Запуск пайплайна v4."""
        logger.info("\n" + "=" * 60)
        logger.info("ФАКТЧЕК ПАЙПЛАЙН v4 — ЗАПУСК")
        logger.info("=" * 60)
        
        # Создаём контекст пайплайна
        context = PipelineContext(
            settings=self.settings,
            source_cache=self.source_cache._cache,
        )
        
        # Загружаем документы
        try:
            docs = load_input_documents(self.settings.input_dir)
            context.input_docs = docs
            logger.info(f"📁 Загружено документов: {len(docs)}")
        except Exception as e:
            logger.error(f"❌ Не удалось загрузить документы: {e}")
            return f"❌ Ошибка загрузки документов: {e}"
        
        if not docs:
            return "❌ Документы не найдены в input/"
        
        # Выполняем граф состояний
        try:
            final_state = self.graph.execute(context)
            
            if not final_state.success:
                logger.error(f"❌ Ошибка выполнения графа: {final_state.error}")
                return f"❌ Ошибка пайплайна: {final_state.error}"
            
            # Возвращаем финальный отчёт
            return context.final_report or "✅ Проверка завершена (отчёт пуст)"
            
        except Exception as e:
            logger.exception(f"❌ Исключение при выполнении графа: {e}")
            return f"❌ Критическая ошибка: {e}"
        
        finally:
            # Логируем итоговую статистику
            logger.info("\n" + "=" * 60)
            logger.info("ИТОГИ СЕССИИ")
            logger.info("=" * 60)
            logger.info(f"Токенов использовано: {context.usage.total_tokens}")
            logger.info(f"Время сессии: {context.usage.total_time:.2f}s")
            logger.info(f"Кеш источников: {len(context.source_cache)} записей")
            logger.info("=" * 60)


def run_v4():
    """Точка входа для запуска v4."""
    orchestrator = FactcheckOrchestratorV4()
    report = orchestrator.run_batch()
    
    # Выводим отчёт
    print("\n" + "=" * 60)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 60)
    print(report)
    
    return report
