"""
Узел 7: Суммаризация отчётов.
Программная суммаризация перед арбитражем (без передачи полных текстов).
"""
import logging

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.nodes.summarization")


def summarize_reports(context: PipelineContext) -> PipelineState:
    """
    Суммаризирует отчёты чекеров для арбитража.
    
    Вместо передачи полных отчётов арбитру:
        1. Извлекаем структурированные данные (таблицы ошибок, списки источников)
        2. Формируем краткое резюме по каждому чекеру
        3. Передаём арбитру только структурированные данные
    
    Экономия токенов: с ~14000 до ~3000-5000.
    """
    logger.info("📝 Суммаризация отчётов чекеров")
    
    if not context.checker_reports:
        logger.warning("⚠️ Нет отчётов чекеров для суммаризации")
        context.summarized_reports = ""
        return PipelineState(context=context)
    
    summarized_parts = []
    
    for report in context.checker_reports:
        # Извлекаем ключевые данные из отчёта
        summary_parts = [
            f"## Чекер {report.checker_idx} ({report.model})",
            f"Фактов проверено: {len(report.facts_checked)}",
            f"Источников найдено: {len(report.sources)}",
        ]
        
        # Краткое резюме (в полной версии — программное извлечение из structured output)
        summary_parts.append(f"Резюме: {report.report[:500]}..." if len(report.report) > 500 else f"Резюме: {report.report}")
        
        # Добавляем источники
        if report.sources:
            summary_parts.append(f"Источники: {', '.join(report.sources[:5])}{'...' if len(report.sources) > 5 else ''}")
        
        summarized_parts.append("\n".join(summary_parts))
    
    context.summarized_reports = "\n\n---\n\n".join(summarized_parts)
    
    logger.info(f"✅ Суммаризация завершена: {len(context.summarized_reports)} симв.")
    
    return PipelineState(context=context)
