"""
Узел 9: Генерация итогового отчёта.
Сохраняет финальный отчёт в output/.
"""
import logging
from pathlib import Path

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.nodes.report")


def generate_report(context: PipelineContext) -> PipelineState:
    """
    Генерирует и сохраняет итоговый отчёт.
    """
    logger.info("📄 Генерация итогового отчёта")
    
    if not context.final_report:
        logger.warning("⚠️ Итоговый отчёт пуст")
        context.final_report = "❌ Проверка не выполнена"
    
    # Сохраняем отчёт
    try:
        report_path: Path = context.settings.output_dir / "check_v31.md"
        report_path.write_text(context.final_report, encoding="utf-8")
        logger.info(f"✅ Отчёт сохранён: {report_path}")
        
        # Логируем метрики
        logger.info("\n" + "=" * 50)
        logger.info("📊 МЕТРИКИ СЕССИИ")
        logger.info("=" * 50)
        logger.info(f"Документов: {len(context.input_docs)}")
        logger.info(f"Чанков: {len(context.chunks)}")
        logger.info(f"Фактов извлечено: {len(context.facts)}")
        logger.info(f"Отчётов чекеров: {len(context.checker_reports)}")
        logger.info(f"Токенов использовано: {context.usage.total_tokens}")
        logger.info(f"Время сессии: {context.usage.total_time:.2f}s")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"❌ Не удалось сохранить отчёт: {e}")
        return PipelineState(
            success=False,
            error=f"Не удалось сохранить отчёт: {e}"
        )
    
    return PipelineState(context=context)
