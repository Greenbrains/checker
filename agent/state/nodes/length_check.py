"""
Узел 3: Проверка длины текста. Решает, нужна ли нарезка на чанки.
"""
import logging

from agent.state.context import Chunk, PipelineContext, PipelineState
from agent.state.text_utils import normalize_text

logger = logging.getLogger("agent.state.nodes.length_check")

DEFAULT_CHUNKING_THRESHOLD = 2000
DEFAULT_CHUNK_CONTEXT = (
    "Проверь все фактические утверждения: даты, числа, названия, цитаты, "
    "направление динамики."
)


def check_length(context: PipelineContext) -> PipelineState:
    """next_node: needs_chunking | no_chunking_needed."""
    logger.info("📏 Проверка длины текста")

    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)

    text = normalize_text(context.extracted_text)
    context.extracted_text = text
    text_length = len(text)
    context.text_length = text_length
    threshold = int(getattr(context.settings, "chunking_threshold", DEFAULT_CHUNKING_THRESHOLD))

    if not text:
        return PipelineState(success=False, error="Текст пуст — проверять нечего", context=context)

    logger.info("Длина текста: %d симв., порог: %d", text_length, threshold)

    if text_length > threshold:
        logger.info("📄 Текст длинный → нарезка на чанки")
        return PipelineState(context=context, next_node="needs_chunking")

    logger.info("✅ Текст короткий → один чанк без нарезки")
    context.chunks = [Chunk(index=1, context=DEFAULT_CHUNK_CONTEXT, text=text)]
    return PipelineState(context=context, next_node="no_chunking_needed")