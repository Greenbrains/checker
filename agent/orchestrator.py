"""
FactcheckOrchestrator — мультиагентный фактчек (пайплайн v1).
Version: 1.3.0
Description: N параллельных чекеров с web_search/web_read/code_execute → арбитр → итоговый отчёт.
Изменения 1.3.0: MIN_REPORT_CHARS вынесена в base.py; убраны магические числа.
"""
import logging
import re
from pathlib import Path
from typing import Dict, List

from agent.base import BaseAgent, UsageTracker, MIN_REPORT_CHARS
from agent.core.prompts.loader import PromptLoader
from agent.core.readers.input_reader import build_user_message, load_input_documents
from agent.core.tools.agent_tools import load_skill
from agent.core.tools.registry import ToolRegistry

logger = logging.getLogger("agent.orchestrator")

ARBITER_ADDENDUM = """
РЕЖИМ АРБИТРА (сведение отчётов чекеров)
Ты сводишь отчёты нескольких чекеров в один итоговый отчёт.
Формат итогового отчёта:
Итоговый отчёт фактчека
1. Сводная таблица ошибок
| № | Файл и цитата | Тип ошибки | Корректное значение | Согласие чекеров | Источники |
2. Детали по каждой ошибке (цитата, почему ошибка, корректно, источники, кто из чекеров нашёл)
3. Расхождения между чекерами и их разрешение по правилам навыка
4. Тезисы без ошибок; непроверяемые тезисы (оценочные/общие фразы)
5. Вывод
Помни:
- Не размечай лишнего; если источники нельзя ранжировать — ошибкой НЕ считаем.
- Если чекер привёл данные первоисточника с рабочим URL — прими их как корректное
  значение, даже если второй чекер молчал («не нашёл» — не опровержение).
- Ошибки в официальных названиях и в направлении динамики («выросла/снизилась»,
  «обогнала/не обогнала») проверяемы по URL первоисточника из отчёта чекера —
  не снимай их только потому, что их заметил лишь один чекер.
- Отчёт чекера вида «чекер недоступен / проверка не выполнена» исключается из
  сведения как вклад, а не как расхождение по существу.
"""


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:40]


def _report_broken(report: str) -> bool:
    r = (report or "").strip().lower()
    if len(r) < MIN_REPORT_CHARS:
        return True
    if "не удалось найти файл" in r or "предоставьте текст" in r:
        return True
    return not any(m in r for m in ("ошибк", "отчёт", "тезис"))


class FactcheckOrchestrator:
    def __init__(self, client, settings, registry: ToolRegistry):
        self.client = client
        self.settings = settings
        self.registry = registry
        self.prompt_loader = PromptLoader()
        self.usage = UsageTracker()
        self.skill_context = load_skill("fact-checking")
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        self.tools_schema, self.tool_router = self.registry.get_tools_for_skill("fact-checking")
        logger.info(f"🔧 Инструменты для чекеров: {[t['function']['name'] for t in self.tools_schema]}")

    @property
    def _max_iter(self) -> int:
        return getattr(self.settings, "checker_max_iterations", 8)

    def _system_prompt(self, addendum: str = "") -> str:
        return self.prompt_loader.render_system_prompt(skill_context=self.skill_context) + addendum

    def _make_checker(self, idx: int, model: str) -> BaseAgent:
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=self._system_prompt(),
            tools_schema=self.tools_schema,
            tool_router=self.tool_router,
            usage_tracker=self.usage,
            role_name=f"checker-{idx}",
            temperature=0.1,
        )

    def _run_checker(self, idx: int, model: str, docs) -> str:
        msg = build_user_message(docs, images=True)
        try:
            report = self._make_checker(idx, model).run(msg, max_iterations=self._max_iter)
        except Exception as e:
            logger.warning(f"⚠️ Чекер {idx} ({model}): сбой мультимодо ({e}); повтор текстом")
            try:
                report = self._make_checker(idx, model).run(
                    build_user_message(docs, images=False),
                    max_iterations=self._max_iter,
                )
            except Exception as e2:
                logger.error(f"❌ Чекер {idx} ({model}) недоступен: {e2}")
                return f"❌ Чекер недоступен (API error): {e2}. Проверка не выполнена."

        if _report_broken(report):
            logger.warning(f"⚠️ Чекер {idx}: отчёт пустой/не по формату — повтор с напоминанием")
            nudge = (
                "\n\n[Система: материалы приведены в сообщении целиком. "
                "НЕ вызывай file_read для их поиска. Составь отчёт по формату навыка.]"
            )
            try:
                report2 = self._make_checker(idx, model).run(
                    msg + nudge, max_iterations=self._max_iter
                )
                if not _report_broken(report2):
                    report = report2
            except Exception as e:
                logger.warning(f"⚠️ Повтор чекера {idx} не удался: {e}")
        return report

    def run_batch(self) -> str:
        docs = load_input_documents(self.settings.input_dir)
        models = self.settings.checker_model_list
        logger.info(f"🔎 [pipeline v1] Чекеров в прогоне: {len(models)}: {models}")

        reports: List[Dict] = []
        for idx, model in enumerate(models, 1):
            logger.info(f"=== Чекер {idx}/{len(models)}: {model} ===")
            report = self._run_checker(idx, model, docs)
            path: Path = self.settings.output_dir / f"check_{idx}_{_slug(model)}.md"
            path.write_text(f"# Отчёт чекера {idx} ({model})\n\n{report}\n", encoding="utf-8")
            logger.info(f"💾 Сохранён отчёт чекера: {path}")
            reports.append({"idx": idx, "model": model, "report": report})

        final = self._arbitrate(reports)
        joined = "\n\n".join(
            f"## ПЕРВИЧНЫЙ ОТЧЁТ ЧЕКЕРА {r['idx']} ({r['model']})\n\n{r['report']}"
            for r in reports
        )
        full = final + "\n\n---\n\n# Приложение. Первичные отчёты чекеров\n\n" + joined
        out = self.settings.output_dir / "check.md"
        out.write_text(full, encoding="utf-8")
        logger.info(f"💾 Итоговый отчёт: {out}")
        logger.info(f"\n{self.usage.summary()}")
        return full

    def _arbitrate(self, reports: List[Dict]) -> str:
        joined = "\n\n".join(
            f"## ОТЧЁТ ЧЕКЕРА {r['idx']} ({r['model']})\n\n{r['report']}"
            for r in reports
        )
        arbiter = BaseAgent(
            client=self.client,
            model=self.settings.arbiter,
            system_prompt=self._system_prompt(ARBITER_ADDENDUM),
            usage_tracker=self.usage,
            role_name="arbiter",
            temperature=0.1,
        )
        task = (
            f"Ниже — отчёты {len(reports)} чекеров по ОДНИМ материалам. "
            f"Сведи их в единый итоговый отчёт по формату режима арбитра.\n\n" + joined
        )
        return arbiter.run(task, max_iterations=3)
