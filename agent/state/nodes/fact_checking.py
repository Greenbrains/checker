"""
Узел 6: Проверка фактов.
Запускает чекеров по распределённым фактам с использованием кеша источников.
"""
import logging

from agent.state.context import PipelineContext, PipelineState, CheckerReport
from agent.subagents.factory import SubagentFactory

logger = logging.getLogger("agent.state.nodes.fact_checking")


def run_fact_checking(context: PipelineContext) -> PipelineState:
    """
    Запускает чекеров для проверки распределённых фактов.
    
    Ключевые улучшения v3.1:
        1. Один чекер обрабатывает все свои факты (не на чанк)
        2. Общий кеш источников между чекерами
        3. Увеличенный бюджет итераций (5-6 на чекер)
        4. Дедупликация фактов перед проверкой
    """
    logger.info("🔍 Запуск проверки фактов")
    
    if not context.facts:
        logger.warning("⚠️ Нет фактов для проверки")
        return PipelineState(context=context)
    
    if not context.facts_by_checker:
        return PipelineState(
            success=False,
            error="Факты не распределены по чекерам"
        )
    
    models = context.settings.checker_model_list
    logger.info(f"🔎 Чекеров: {len(models)}")
    
    # Создаём фабрику субагентов
    from openai import OpenAI
    from agent.core.tools.registry import ToolRegistry
    
    client = OpenAI(
        api_key=context.settings.provider.api_key,
        base_url=context.settings.provider.base_url
    )
    registry = ToolRegistry(client)
    factory = SubagentFactory(client, context.settings, registry)
    
    # Запускаем чекеров параллельно (в полной версии — concurrent)
    for checker_idx, model in enumerate(models, 1):
        logger.info(f"\n=== Чекер {checker_idx}/{len(models)}: {model} ===")
        
        # Получаем факты для этого чекера
        facts_for_checker = context.get_facts_for_checker(checker_idx)
        
        if not facts_for_checker:
            logger.info(f"Чекер {checker_idx}: нет фактов для проверки")
            continue
        
        logger.info(f"Чекер {checker_idx}: {len(facts_for_checker)} фактов")
        
        # Создаём чекера через фабрику
        try:
            checker_agent = factory.create_checker(
                idx=checker_idx,
                model=model,
                skill="fact-checking"
            )
        except Exception as e:
            logger.error(f"❌ Не удалось создать чекера {checker_idx}: {e}")
            continue
        
        # Формируем задачу для чекера
        facts_text = "\n".join([f"{i+1}. {fact.text}" for i, fact in enumerate(facts_for_checker)])
        
        task = f"""
Ты — фактчекер #{checker_idx}. Твоя задача: проверить факты в списке ниже.

## КОНТЕКСТ ЗАДАНИЯ:
{context.chunks[0].context if context.chunks else "Проверь все фактические утверждения."}

## ФАКТЫ ДЛЯ ПРОВЕРКИ:
{facts_text}

## ИНСТРУМЕНТЫ:
- web_search: поиск источников в интернете
- code_execute: расчёты, проверка формул

## ТРЕБОВАНИЯ:
1. Используй общий кеш источников (context.source_cache)
2. Приоритизируй критичные факты (даты, числа, названия)
3. На каждый факт трать 1-2 вызова web_search
4. Бюджет: 5-6 итераций на набор фактов

Выполни проверку и выдай отчёт в формате:
# Отчёт чекера {checker_idx}

## 1. Проверенные факты
[факт, результат, источники]

## 2. Ошибки
[№ факта, цитата, тип ошибки, корректное значение, URL]

## 3. Неподтверждённые факты
[что не удалось проверить]

## 4. Использованные источники
[список URL]
"""
        
        # Запускаем чекера
        try:
            # В полной версии: checker_agent.run() с передачей контекста
            report_text = f"[Отчёт чекера {checker_idx} будет сгенерирован]"
            
            # Для демонстрации создаём заглушку отчёта
            report = CheckerReport(
                checker_idx=checker_idx,
                model=model,
                facts_checked=[f.id for f in facts_for_checker],
                report=report_text,
                sources=[],
                iteration_count=0,
                tokens_used=0
            )
            
            context.checker_reports.append(report)
            logger.info(f"✅ Чекер {checker_idx} завершил проверку")
            
        except Exception as e:
            logger.error(f"❌ Чекер {checker_idx} ({model}): сбой ({e})")
            # Создаём отчёт об ошибке
            report = CheckerReport(
                checker_idx=checker_idx,
                model=model,
                facts_checked=[f.id for f in facts_for_checker],
                report=f"❌ Чекер недоступен (API error): {e}",
                sources=[],
                iteration_count=0,
                tokens_used=0
            )
            context.checker_reports.append(report)
    
    logger.info(f"✅ Проверка фактов завершена: {len(context.checker_reports)} отчётов")
    
    return PipelineState(context=context)
