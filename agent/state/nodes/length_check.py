"""
Узел 3: Проверка длины текста.
Решает, нужна ли нарезка на чанки.
"""
import logging

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.nodes.length_check")

# Порог длины для нарезки (символы)
DEFAULT_CHUNKING_THRESHOLD = 2000


def check_length(context: PipelineContext) -> PipelineState:
    """
    Проверяет длину текста и решает, нужна ли нарезка.
    
    Возвращает:
        next_node: "needs_chunking" если текст длинный, иначе "no_chunking_needed"
    """
    logger.info("📏 Проверка длины текста")
    
    text_length = context.text_length or len(context.extracted_text)
    threshold = getattr(context.settings, 'chunking_threshold', DEFAULT_CHUNKING_THRESHOLD)
    
    logger.info(f"Длина текста: {text_length} симв., порог: {threshold}")
    
    if text_length > threshold:
        logger.info(f"📄 Текст длинный ({text_length} > {threshold}) → требуется нарезка")
        return PipelineState(
            context=context,
            next_node="needs_chunking"
        )
    else:
        logger.info(f"✅ Текст короткий ({text_length} <= {threshold}) → без нарезки")
        # Создаём один чанк из всего текста
        from agent.state.context import Chunk
        context.chunks = [
            Chunk(
                index=1,
                context="Проверь все фактические утверждения: даты, числа, названия, цитаты, направления динамики.",
                text=context.extracted_text
            )
        ]
        return PipelineState(
            context=context,
            next_node="no_chunking_needed"
        )
