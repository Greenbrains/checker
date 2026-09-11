"""
Узел 1: Анализ входа. Определяет тип документов и маршрут (условие перехода).
"""
import logging
from collections import Counter

from agent.state.context import PipelineContext, PipelineState
from agent.state.text_utils import normalize_text

logger = logging.getLogger("agent.state.nodes.input_analysis")


def analyze_input(context: PipelineContext) -> PipelineState:
    """Определяет тип входных документов. next_node: image_or_table | text_only."""
    logger.info("📥 Анализ входных документов")

    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)

    docs = context.input_docs or []
    if not docs:
        from agent.core.readers.input_reader import load_input_documents
        try:
            docs = load_input_documents(context.settings.input_dir)
        except Exception as e:
            return PipelineState(success=False, error=f"Не удалось загрузить документы: {e}", context=context)
        context.input_docs = docs

    if not docs:
        return PipelineState(
            success=False,
            error=f"Входные документы не найдены в {context.settings.input_dir}/",
            context=context,
        )

    kinds = Counter()
    text_parts = []
    for doc in docs:
        name = doc.get("name", "unknown")
        kind = str(doc.get("kind") or "text").lower()
        kinds[kind] += 1
        content = normalize_text(doc.get("content", "") or "")
        logger.info("  • %s → %s, %d симв.", name, kind, len(content))
        if kind == "text":
            text_parts.append(f"--- Текст: {name} ---\n{content}")
        elif doc.get("text"):  # таблицы/картинки, если ридер уже дал текст
            text_parts.append(f"--- {kind}: {name} ---\n{normalize_text(doc['text'])}")

    if text_parts:
        context.extracted_text = "\n\n".join(text_parts)
    context.text_length = len(context.extracted_text)
    context.metadata["input_kinds"] = dict(kinds)
    logger.info("✍️ Извлечён текст: %d симв. (%s)", context.text_length, dict(kinds))

    needs_reader = kinds.get("image", 0) or kinds.get("table", 0)
    if needs_reader and not text_parts:
        logger.info("🖼️ Нужен ридер → text_extraction")
        return PipelineState(context=context, next_node="image_or_table")
    if needs_reader:
        logger.info("🖼️ Есть текст, но остались картинки/таблицы → text_extraction")
        return PipelineState(context=context, next_node="image_or_table")
    logger.info("📄 Только текст → length_check")
    return PipelineState(context=context, next_node="text_only")