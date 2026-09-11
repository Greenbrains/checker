"""
Узел 6: Проверка фактов чекерами (РЕАЛЬНЫЙ вызов LLM, не заглушка).
Version: 2.0.0
Изменения 2.0.0:
    - убраны заглушки "[Отчёт чекера N будет сгенерирован]" — агент создаётся И запускается;
    - убрана зависимость от SubagentFactory: BaseAgent собирается напрямую, как в рабочем v3;
    - клиент и ToolRegistry берутся из context.metadata (без пересоздания);
    - web_search обёрнут кешем сессии (context.source_cache);
    - чекеры идут параллельно (ThreadPoolExecutor), токены каждого мержатся в context.usage;
    - повторный запрос отчёта, если он пустой/не по формату.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from agent.base import BaseAgent, UsageTracker
from agent.state.context import CheckerReport, Fact, PipelineContext, PipelineState
from agent.state.prompts import build_system_prompt
from agent.state.text_utils import extract_urls, report_broken

logger = logging.getLogger("agent.state.nodes.fact_checking")

CHECKER_ADDENDUM = """
РЕЖИМ ЧЕКЕРА (v3.1)

Ты проверяешь список фактов, извлечённых из одного документа. Факты сгруппированы
по чанкам, каждый помечен [ID], типом и приоритетом (‼️ critical / • normal).

Правила:
1. Идёшь по фактам сверху вниз, критичные — в первую очередь.
2. На один факт — 1–2 вызова web_search. Два пустых поиска подряд → меняй формулировку.
3. Не повторяй уже сделанные запросы: результаты поиска кешируются на сессию.
4. Источник без рабочего URL не считается подтверждением.
5. Бюджет: {max_iter} итераций на весь список. Исчерпал — пиши отчёт по собранному.
6. Оформляй отчёт строго по шаблону ниже, URL приводи полностью.

ФОРМАТ ОТЧЁТА:
# Отчёт чекера {idx}

## 1. Проверенные факты
[ID факта — вердикт: подтверждено / ошибка / не проверено — URL]

## 2. Ошибки
| № | ID факта | Цитата | Тип ошибки | Корректное значение | Источники |

## 3. Неподтверждённые факты
[ID и что именно не удалось найти]

## 4. Использованные источники
[полные URL, по одному на строку]

## 5. Вывод
[сколько фактов проверено, сколько ошибок, какие критичные]
"""

NUDGE = (
    "\n\n[Система: факты и контекст уже приведены выше целиком. "
    "НЕ ищи материалы файлами. Составь отчёт строго по формату навыка.]"
)


def _cached_web_search(context: PipelineContext, base_search):
    """Оборачивает web_search кешем сессии (context.source_cache)."""
    def wrapper(query: str, max_results: int = 5):
        key = f"web_search::{query.strip().lower()}::{max_results}"
        cached = context.cache_get(key)
        if cached is not None:
            return f"{cached}\n\n[источник: кеш сессии]"
        result = base_search(query=query, max_results=max_results)
        if isinstance(result, str) and not result.startswith("❌"):
            context.cache_put(key, result)
        return result

    wrapper._tool_name = "web_search"
    wrapper._tool_schema = getattr(base_search, "_tool_schema", None)
    return wrapper


def _build_task(context: PipelineContext, checker_idx: int,
                facts: List[Fact], max_iter: int, model: str) -> str:
    chunks_by_index: Dict[int, str] = {c.index: c.text for c in context.chunks}
    critical = sum(1 for f in facts if f.priority == "critical")

    blocks = []
    for fact in facts:
        src = chunks_by_index.get(fact.chunk_index, "")
        snippet = src if len(src) <= 400 else src[:400] + "…"
        blocks.append(f"{fact.prompt_line()}\n   ← контекст (чанк {fact.chunk_index}): {snippet}")

    task_context = context.chunks[0].context if context.chunks else "Проверь фактические утверждения."
    return f"""Ты — фактчекер #{checker_idx} (модель: {model}).

## КОНТЕКСТ ЗАДАНИЯ
{task_context}

## ФАКТЫ ДЛЯ ПРОВЕРКИ ({len(facts)} шт., критичных: {critical})
{chr(10).join(blocks)}

## ИНСТРУМЕНТЫ
- web_search(query, max_results) — поиск первоисточников
- code_execute(code) — расчёты, даты, проверка таблиц

## БЮДЖЕТ
Не более {max_iter} итераций с инструментами.

Выполни проверку и выдай отчёт по формату из системного промпта.
"""


def _run_one_checker(context: PipelineContext, checker_idx: int, model: str,
                     schemas, router, max_iter: int) -> Tuple[CheckerReport, UsageTracker]:
    """Проверка фактов одного чекера в отдельном потоке. Общих мутаций контекста нет."""
    tracker = UsageTracker()
    facts = context.get_facts_for_checker(checker_idx)

    if not facts:
        logger.info("Чекер %d: фактов не назначено", checker_idx)
        return CheckerReport(checker_idx, model, [], "— фактов не назначено —"), tracker

    agent = BaseAgent(
        client=context.metadata.get("client"),
        model=model,
        system_prompt=build_system_prompt(
            CHECKER_ADDENDUM.format(max_iter=max_iter, idx=checker_idx)
        ),
        tools_schema=schemas,
        tool_router=router,
        usage_tracker=tracker,
        role_name=f"checker-{checker_idx}",
        temperature=0.1,
    )
    task = _build_task(context, checker_idx, facts, max_iter, model)

    logger.info("=== Чекер %d/%s: %d фактов, бюджет %d итераций ===",
                checker_idx, model, len(facts), max_iter)
    try:
        report = agent.run(task, max_iterations=max_iter)
    except Exception as e:
        logger.error("❌ Чекер %d (%s): API-сбой: %s", checker_idx, model, e)
        report = f"❌ Чекер недоступен (API error): {e}. Проверка не выполнена."

    if report_broken(report):
        logger.warning("⚠️ Чекер %d: отчёт пустой/не по формату — повтор", checker_idx)
        try:
            retry = agent.run(task + NUDGE, max_iterations=max(2, max_iter // 2))
            if not report_broken(retry):
                report = retry
        except Exception as e:
            logger.warning("⚠️ Повтор чекера %d не удался: %s", checker_idx, e)

    logger.info("✅ Чекер %d завершил: %d симв., %d запросов, %d токенов",
                checker_idx, len(report), tracker.request_count, tracker.total_tokens)

    return CheckerReport(
        checker_idx=checker_idx,
        model=model,
        facts_checked=[f.id for f in facts],
        report=report,
        sources=extract_urls(report),
        iteration_count=tracker.request_count,
        tokens_used=tracker.total_tokens,
    ), tracker


def run_fact_checking(context: PipelineContext) -> PipelineState:
    """Запускает чекеров (параллельно) и собирает их отчёты в контекст."""
    logger.info("🔍 Запуск проверки фактов")

    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)
    if not context.facts:
        logger.warning("⚠️ Нет фактов для проверки")
        return PipelineState(context=context)
    if not context.facts_by_checker:
        return PipelineState(success=False, error="Факты не распределены по чекерам", context=context)

    client = context.metadata.get("client")
    registry = context.metadata.get("registry")
    if client is None:
        from openai import OpenAI
        p = context.settings.provider
        client = OpenAI(api_key=p.api_key, base_url=p.base_url,
                        timeout=getattr(context.settings, "api_timeout", 120.0))
        context.metadata["client"] = client
    if registry is None:
        from agent.core.tools.registry import ToolRegistry
        registry = ToolRegistry(client)
        context.metadata["registry"] = registry

    schemas, router = registry.get_tools_by_names({"web_search", "code_execute"})
    if "web_search" in router:
        router = dict(router)
        router["web_search"] = _cached_web_search(context, router["web_search"])

    models = context.settings.checker_model_list
    max_iter = max(3, int(context.settings.checker_max_iterations))
    parallel = bool(getattr(context.settings, "checker_parallel", True)) and len(models) > 1
    timeout = int(getattr(context.settings, "checker_timeout", 900))

    logger.info("🔎 Чекеров: %d (%s), инструменты: %s, протокол: %s",
                len(models),
                ", ".join(models),
                [s["function"]["name"] for s in schemas],
                "параллельно" if parallel else "последовательно")

    reports: List[CheckerReport] = []

    def _fallback(idx: int, model: str, err: str) -> CheckerReport:
        return CheckerReport(idx, model, [], f"❌ Чекер недоступен: {err}. Проверка не выполнена.")

    if parallel:
        with ThreadPoolExecutor(max_workers=len(models)) as pool:
            futures = {
                pool.submit(_run_one_checker, context, idx, model, schemas, router, max_iter): (idx, model)
                for idx, model in enumerate(models, 1)
            }
            for fut in as_completed(futures):
                idx, model = futures[fut]
                try:
                    report, tracker = fut.result(timeout=timeout)
                    context.usage.merge(tracker)
                    reports.append(report)
                except Exception as e:
                    logger.error("❌ Чекер %d (%s): %s", idx, model, e)
                    reports.append(_fallback(idx, model, str(e)))
    else:
        for idx, model in enumerate(models, 1):
            try:
                report, tracker = _run_one_checker(context, idx, model, schemas, router, max_iter)
            except Exception as e:
                logger.error("❌ Чекер %d (%s): %s", idx, model, e)
                report, tracker = _fallback(idx, model, str(e)), UsageTracker()
            context.usage.merge(tracker)
            reports.append(report)

    reports.sort(key=lambda r: r.checker_idx)
    context.checker_reports = reports

    for r in reports:
        logger.info("Чекер %d: %d симв. отчёта, %d источников, %d токенов",
                    r.checker_idx, len(r.report), len(r.sources), r.tokens_used)
    logger.info("✅ Проверка фактов завершена: %d отчётов", len(reports))
    return PipelineState(context=context)