"""
FactcheckOrchestratorV3 — пайплайн с state-агентами: эмбеддер → чекеры → арбитр.
Version: 3.0.0
Description:
    - Оркестратор (он же арбитр) управляет стейтами;
    - Стейт 1: Эмбеддер — извлекает текст из файлов, режет на чанки по 7-10 предложений, сохраняет в MD;
    - Стейт 2: Чекеры — каждый читает чанки, проверяет факты, пишет отчёт по чанку;
    - Стейт 3: Арбитр — сводит отчёты чекеров в краткий итоговый отчёт;
    - Удалён web_read из инструментов (не используется);
    - Чекеры: web_search (поиск источников), code_execute (расчёты, таблицы);
    - Оптимизация итераций: чекеры приоритизируют факты, ограничивают число итераций на чанк;
    - В output: отчёты всех чекеров + краткий отчёт арбитра.
"""
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, field

from agent.base import BaseAgent, UsageTracker, MIN_REPORT_CHARS
from agent.core.prompts.loader import PromptLoader
from agent.core.readers.input_reader import load_input_documents
from agent.core.tools.agent_tools import load_skill
from agent.core.tools.registry import ToolRegistry

logger = logging.getLogger("agent.orchestrator_v3")

# Расширения файлов, которые оркестратор умеет обрабатывать
SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".text", ".log",  # текст
    ".csv",  # таблицы CSV
    ".xlsx", ".xls",  # Excel
    ".doc", ".docx",  # Word
    ".ppt", ".pptx",  # PowerPoint
    ".pdf",  # PDF
    ".png", ".jpg", ".jpeg", ".gif", ".webp",  # изображения
}

EMBEDDER_SYSTEM_PROMPT = """
Ты — эмбеддер. Твоя задача:
1. Извлечь текст из предоставленных материалов.
2. Разбить текст на чанки по 7-10 предложений каждый.
3. Каждый чанк пронумеровать и сохранить в формате Markdown.
4. Добавить контекст для каждого чанка (номер, общий контекст задания).

Формат вывода для каждого чанка:
---
## Чанк N
Контекст: [общий контекст задания]
Текст для проверки:
[текст чанка]
---

Не добавляй ничего лишнего. Просто верни чанки в указанном формате.
"""

CHECKER_ADDENDUM_V3 = """
РЕЖИМ ЧЕКЕРА — ОПТИМИЗИРОВАННЫЙ

Ты проверяешь факты в предоставленном чанке текста.

Правила:
1. Сначала выдели ВСЕ факты для проверки (пронумеруй тезисы).
2. Приоритизируй: критичные (числа, даты, названия, цитаты) → второстепенные.
3. На каждый факт трать НЕ БОЛЕЕ 1-2 вызовов web_search.
4. Бюджет: {max_iter} итераций НА ЧАНК. Исчерпал — пиши отчёт с тем, что добыл.
5. Не повторяй одинаковые запросы. Два поиска без результата — меняй стратегию.
6. Найди 2-3 источника по каждому проверяемому тезису.

ФОРМАТ ОТЧЁТА ПО ЧАНКУ:
# Отчёт чекера N по чанку M

## 1. Выделенные тезисы для проверки
[список тезисов с номерами]

## 2. Ошибки
По каждой: № тезиса, цитата, тип ошибки, почему ошибка, корректное значение, URL источников.

## 3. Проверено без ошибок
[утверждения с URL источников]

## 4. Неподтверждённые тезисы
[что не удалось проверить]

## 5. Использованные источники
[список всех URL, использованных в отчёте]

## 6. Вывод по чанку
[краткий итог: сколько ошибок, какие критичные]
"""

ARBITER_ADDENDUM_V3 = """
РЕЖИМ АРБИТРА (сведение отчётов чекеров) — КРАТКИЙ ФОРМАТ

Ты сводишь отчёты нескольких чекеров в один итоговый отчёт в КРАТКОМ формате:

# Итоговый отчёт фактчека (краткий)

## 1. Сводная таблица ошибок
| № | Цитата | Тип ошибки | Корректное значение | Согласие чекеров | Источники |

## 2. Ссылки на источники по чекерам
- Чекер 1 (model_name): [список URL из отчёта]
- Чекер 2 (model_name): [список URL из отчёта]
- ...

## 3. Выводы по чекерам (кратко, без погружения)
- Чекер 1: [основной вывод, сколько ошибок нашёл, ключевые источники]
- Чекер 2: [основной вывод, сколько ошибок нашёл, ключевые источники]
- ...

## 4. Общий вывод
[Итоговое заключение: сколько всего ошибок найдено, какие критичные, общий вердикт]

Правила сведения:
- Не размечай лишнего; если источники нельзя ранжировать — ошибкой НЕ считаем.
- Если чекер привёл данные первоисточника с рабочим URL — прими их как корректное значение.
- Ошибки в официальных названиях и направлении динамики проверяемы по URL первоисточника.
- Отчёт чекера вида «чекер недоступен / проверка не выполнена» исключается из сведения.
- Фокус на фактических ошибках, не на стилистике.
"""


@dataclass
class Chunk:
    """Чанк текста для проверки."""
    index: int
    context: str
    text: str


@dataclass
class CheckerReport:
    """Отчёт чекера по чанку."""
    checker_idx: int
    model: str
    chunk_index: int
    report: str
    sources: List[str] = field(default_factory=list)


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:40]


def _report_broken(report: str) -> bool:
    r = (report or "").strip().lower()
    if len(r) < MIN_REPORT_CHARS:
        return True
    if "не удалось найти файл" in r or "предоставьте текст" in r:
        return True
    return not any(m in r for m in ("ошибк", "отчёт", "тезис"))


def _split_into_chunks(text: str, sentences_per_chunk: int = 8) -> List[str]:
    """
    Разбивает текст на чанки по предложениям.
    sentences_per_chunk: количество предложений в чанке (7-10).
    """
    # Простая эвристика: разбиваем по точкам, восклицательным и вопросительным знакам
    # с учётом переносов строк
    import re
    
    # Разбиваем на предложения (учитываем . ! ? \n)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    chunks = []
    for i in range(0, len(sentences), sentences_per_chunk):
        chunk_sentences = sentences[i:i + sentences_per_chunk]
        chunk_text = ' '.join(chunk_sentences)
        if chunk_text.strip():
            chunks.append(chunk_text)
    
    return chunks


def _extract_text_from_docs(docs) -> str:
    """Извлекает текст из документов."""
    text_parts = []
    for doc in docs:
        kind = doc.get("kind", "text")
        content = doc.get("content", "")
        name = doc.get("name", "unknown")
        
        if kind == "image":
            text_parts.append(f"[Изображение: {name} — распознано оркестратором]")
        elif kind == "table":
            text_parts.append(f"--- Таблица: {name} ---\n{content}")
        else:
            text_parts.append(f"--- Текст: {name} ---\n{content}")
    
    return "\n\n".join(text_parts)


class FactcheckOrchestratorV3:
    """Оркестратор v3 со стейт-агентами: эмбеддер → чекеры → арбитр."""
    
    def __init__(self, client, settings, registry: ToolRegistry):
        self.client = client
        self.settings = settings
        self.registry = registry
        self.prompt_loader = PromptLoader()
        self.usage = UsageTracker()
        self.skill_context = load_skill("fact-checking")
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Инструменты для чекеров БЕЗ web_read
        checker_tools = {"web_search", "code_execute"}
        self.tools_schema, self.tool_router = self.registry.get_tools_by_names(checker_tools)
        logger.info(f"🔧 Инструменты для чекеров (v3): {[t['function']['name'] for t in self.tools_schema]}")

    @property
    def _max_iter(self) -> int:
        return getattr(self.settings, "checker_max_iterations", 8)

    def _system_prompt(self, addendum: str = "") -> str:
        return self.prompt_loader.render_system_prompt(skill_context=self.skill_context) + addendum

    def _make_embedder(self, model: str) -> BaseAgent:
        """Создаёт агента-эмбедера."""
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=EMBEDDER_SYSTEM_PROMPT,
            tools_schema=[],  # Эмбеддеру инструменты не нужны
            tool_router={},
            usage_tracker=self.usage,
            role_name="embedder",
            temperature=0.1,
        )

    def _make_checker(self, idx: int, model: str, max_iter: int) -> BaseAgent:
        """Создаёт агента-чекера."""
        addendum = CHECKER_ADDENDUM_V3.format(max_iter=max_iter)
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=self._system_prompt(addendum),
            tools_schema=self.tools_schema,
            tool_router=self.tool_router,
            usage_tracker=self.usage,
            role_name=f"checker-{idx}",
            temperature=0.1,
        )

    def _make_arbiter(self, model: str) -> BaseAgent:
        """Создаёт агента-арбитра."""
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=self._system_prompt(ARBITER_ADDENDUM_V3),
            usage_tracker=self.usage,
            role_name="arbiter",
            temperature=0.1,
        )

    def _run_embedder(self, text: str, context: str, model: str) -> List[Chunk]:
        """
        Стейт 1: Эмбеддер извлекает текст и режет на чанки.
        Возвращает список чанков.
        """
        logger.info(f"📦 [embedder] Запуск на модели={model}")
        
        task = f"""
Материалы для обработки:
{text}

Контекст задания:
{context if context else "Проверь все фактические утверждения: даты, числа, названия, цитаты, направления динамики."}

Разбей текст на чанки по 7-10 предложений. Для каждого чанка укажи:
- Номер чанка
- Контекст задания
- Текст для проверки

Верни результат в формате:
---
## Чанк N
Контекст: [контекст]
Текст для проверки:
[текст]
---
"""
        
        try:
            response = self._make_embedder(model).run(task, max_iterations=3)
        except Exception as e:
            logger.error(f"❌ Эмбеддер ({model}): сбой ({e})")
            # Fallback: простая разбивка без модели
            chunks_text = _split_into_chunks(text, sentences_per_chunk=8)
            response = ""
            for i, chunk in enumerate(chunks_text, 1):
                response += f"\n---\n## Чанк {i}\nКонтекст: {context}\nТекст для проверки:\n{chunk}\n---\n"
        
        # Парсим ответ эмбедера
        chunks = self._parse_chunks(response, context)
        logger.info(f"✅ [embedder] Создано чанков: {len(chunks)}")
        return chunks

    def _parse_chunks(self, response: str, context: str) -> List[Chunk]:
        """Парсит ответ эмбедера в список Chunk."""
        chunks = []
        
        # Простая эвристика: ищем паттерн "## Чанк N"
        import re
        pattern = r'##\s*Чанк\s+(\d+)\s*\nКонтекст:\s*(.*?)\nТекст для проверки:\s*(.*?)(?=##\s*Чанк|\Z)'
        matches = re.findall(pattern, response, re.DOTALL)
        
        if matches:
            for idx, ctx, text in matches:
                chunks.append(Chunk(index=int(idx), context=ctx.strip(), text=text.strip()))
        else:
            # Fallback: разбиваем весь текст на чанки
            fallback_chunks = _split_into_chunks(response, sentences_per_chunk=8)
            for i, text in enumerate(fallback_chunks, 1):
                chunks.append(Chunk(index=i, context=context, text=text))
        
        return chunks

    def _run_checker_on_chunk(self, checker_idx: int, model: str, chunk: Chunk) -> CheckerReport:
        """Запускает чекера на одном чанке."""
        logger.info(f"📋 [checker-{checker_idx}] Проверка чанка {chunk.index}, модель={model}")
        
        task = f"""
Ты — фактчекер. Твоя задача: проверить факты в предоставленном чанке текста.

## КОНТЕКСТ ЗАДАНИЯ:
{chunk.context}

## МАТЕРИАЛ ДЛЯ ПРОВЕРКИ (чанк {chunk.index}):
{chunk.text}

## ИНСТРУМЕНТЫ:
- web_search: поиск источников в интернете (DuckDuckGo → Brave → Google → Yahoo)
- code_execute: расчёты, проверка формул, обработка данных в pandas

Выполни проверку и выдай отчёт в формате, указанном в системном промпте.
"""
        
        max_iter_per_chunk = max(3, self._max_iter // 5)  # Делим бюджет на чанки
        
        try:
            report = self._make_checker(checker_idx, model, max_iter_per_chunk).run(task, max_iterations=max_iter_per_chunk)
        except Exception as e:
            logger.warning(f"⚠️ Чекер {checker_idx} ({model}), чанк {chunk.index}: сбой ({e})")
            report = f"❌ Чекер недоступен (API error): {e}. Проверка чанка {chunk.index} не выполнена."
        
        # Извлекаем источники из отчёта
        sources = self._extract_sources(report)
        
        return CheckerReport(
            checker_idx=checker_idx,
            model=model,
            chunk_index=chunk.index,
            report=report,
            sources=sources,
        )

    def _extract_sources(self, report: str) -> List[str]:
        """Извлекает URL из отчёта."""
        import re
        urls = re.findall(r'https?://[^\s\)]+', report)
        return list(set(urls))

    def _run_checker(self, idx: int, model: str, chunks: List[Chunk]) -> List[CheckerReport]:
        """Запускает чекера на всех чанках."""
        reports = []
        for chunk in chunks:
            report = self._run_checker_on_chunk(idx, model, chunk)
            reports.append(report)
            
            # Сохраняем промежуточный отчёт по чанку
            path: Path = self.settings.output_dir / f"check_{idx}_{_slug(model)}_chunk{chunk.index}.md"
            path.write_text(f"# Отчёт чекера {idx} ({model}) по чанку {chunk.index}\n\n{report.report}\n", encoding="utf-8")
        
        return reports

    def _arbitrate(self, all_reports: List[List[CheckerReport]]) -> str:
        """Стейт 3: Арбитраж — сведение отчётов чекеров."""
        logger.info("⚖️ [arbiter] Сведение отчётов чекеров")
        
        # Группируем отчёты для арбитра
        joined_parts = []
        for reports in all_reports:
            for r in reports:
                joined_parts.append(f"## ОТЧЁТ Чекера {r.checker_idx} ({r.model}) по чанку {r.chunk_index}\n\n{r.report}")
        
        joined = "\n\n".join(joined_parts)
        
        arbiter = self._make_arbiter(self.settings.arbiter)
        task = (
            f"Ниже — отчёты чекеров по разным чанкам ОДНИХ материалов. "
            f"Сведи их в единый итоговый отчёт в КРАТКОМ формате режима арбитра.\n\n" + joined
        )
        
        return arbiter.run(task, max_iterations=3)

    def run_batch(self) -> str:
        """Запуск пайплайна v3."""
        # 1. Загрузка документов
        docs = load_input_documents(self.settings.input_dir)
        logger.info(f"📁 Загружено документов: {len(docs)}")
        
        # 2. Извлечение текста
        extracted_text = _extract_text_from_docs(docs)
        logger.info(f"✍️ Извлечён текст: {len(extracted_text)} симв.")
        
        # 3. Контекст задания
        context = "Проверь все фактические утверждения: даты, числа, названия, цитаты, направления динамики."
        
        # === СТЕЙТ 1: ЭМБЕДДЕР ===
        logger.info("\n=== СТЕЙТ 1: ЭМБЕДДЕР ===")
        embedder_model = self.settings.model_agent  # Используем основную модель
        chunks = self._run_embedder(extracted_text, context, embedder_model)
        logger.info(f"✅ Создано чанков: {len(chunks)}")
        
        # Сохраняем чанки в файлы
        chunks_dir = self.settings.output_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)
        for chunk in chunks:
            chunk_file = chunks_dir / f"chunk_{chunk.index}.md"
            chunk_file.write_text(f"## Чанк {chunk.index}\n\nКонтекст: {chunk.context}\n\nТекст:\n{chunk.text}\n", encoding="utf-8")
        
        # === СТЕЙТ 2: ЧЕКЕРЫ ===
        logger.info("\n=== СТЕЙТ 2: ЧЕКЕРЫ ===")
        models = self.settings.checker_model_list
        logger.info(f"🔎 Чекеров в прогоне: {len(models)}: {models}")
        
        all_reports: List[List[CheckerReport]] = []
        for idx, model in enumerate(models, 1):
            logger.info(f"\n=== Чекер {idx}/{len(models)}: {model} ===")
            reports = self._run_checker(idx, model, chunks)
            all_reports.append(reports)
            logger.info(f"✅ Чекер {idx} завершил проверку {len(reports)} чанков")
        
        # === СТЕЙТ 3: АРБИТР ===
        logger.info("\n=== СТЕЙТ 3: АРБИТР ===")
        final_report = self._arbitrate(all_reports)
        
        # Сборка полного отчёта
        checker_reports_joined = ""
        for reports in all_reports:
            for r in reports:
                checker_reports_joined += f"\n\n## ОТЧЁТ Чекера {r.checker_idx} ({r.model}) по чанку {r.chunk_index}\n\n{r.report}\n"
        
        full = final_report + "\n\n---\n\n# Приложение. Первичные отчёты чекеров по чанкам" + checker_reports_joined
        
        out = self.settings.output_dir / "check.md"
        out.write_text(full, encoding="utf-8")
        logger.info(f"💾 Итоговый отчёт: {out}")
        logger.info(f"\n{self.usage.summary()}")
        
        return full
