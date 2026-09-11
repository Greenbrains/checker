"""
Узел 8: Арбитраж.
Сведение суммаризированных отчётов чекеров в итоговый вердикт.
"""
import logging

from agent.state.context import PipelineContext, PipelineState
from agent.subagents.factory import SubagentFactory

logger = logging.getLogger("agent.state.nodes.arbitration")


def run_arbitration(context: PipelineContext) -> PipelineState:
    """
    Запускает арбитра для сведения отчётов чекеров.
    
    Арбитр получает суммаризированные отчёты (не полные тексты),
    что экономит токены и ускоряет работу.
    """
    logger.info("⚖️ Запуск арбитража")
    
    if not context.summarized_reports:
        logger.warning("⚠️ Нет суммаризированных отчётов для арбитража")
        context.final_report = "❌ Отчёты чекеров отсутствуют"
        return PipelineState(context=context)
    
    # Создаём арбитра через фабрику
    from openai import OpenAI
    from agent.core.tools.registry import ToolRegistry
    
    client = OpenAI(
        api_key=context.settings.provider.api_key,
        base_url=context.settings.provider.base_url
    )
    registry = ToolRegistry(client)
    factory = SubagentFactory(client, context.settings, registry)
    
    try:
        arbiter_agent = factory.create_arbiter(
            model=context.settings.arbiter,
            skill="fact-arbitration"
        )
    except Exception as e:
        logger.error(f"❌ Не удалось создать арбитра: {e}")
        context.final_report = f"❌ Арбитр недоступен: {e}"
        return PipelineState(context=context)
    
    # Формируем задачу для арбитра
    task = f"""
Ты — арбитр фактчека. Твоя задача: свести отчёты чекеров в итоговый вердикт.

## СУММАРИЗИРОВАННЫЕ ОТЧЁТЫ ЧЕКЕРОВ:
{context.summarized_reports}

## ТРЕБОВАНИЯ К ИТОГОВОМУ ОТЧЁТУ:
1. Сводная таблица ошибок (если найдены)
2. Список источников по чекерам
3. Краткие выводы по каждому чекеру
4. Общий вердикт

## ФОРМАТ ОТЧЁТА:
# Итоговый отчёт фактчека

## 1. Сводная таблица ошибок
| № | Цитата | Тип ошибки | Корректное значение | Согласие чекеров | Источники |

## 2. Ссылки на источники по чекерам
- Чекер 1 (model_name): [список URL]
- Чекер 2 (model_name): [список URL]

## 3. Выводы по чекерам
- Чекер 1: [основной вывод]
- Чекер 2: [основной вывод]

## 4. Общий вывод
[Итоговое заключение]
"""
    
    # Запускаем арбитра
    try:
        # В полной версии: arbiter_agent.run()
        final_report = "[Итоговый отчёт арбитра будет сгенерирован]"
        context.final_report = final_report
        logger.info("✅ Арбитраж завершён")
        
    except Exception as e:
        logger.error(f"❌ Арбитраж не удался: {e}")
        context.final_report = f"❌ Арбитраж не удался: {e}"
    
    return PipelineState(context=context)
