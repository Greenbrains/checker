"""
Узел 4: Нарезка текста на чанки. Программная логика, без LLM.
"""
import logging

from agent.state.context import Chunk, PipelineContext, PipelineState
from agent.state.text_utils import split_into_chunks

logger = logging.getLogger("agent.state.nodes.chunking")

DEFAULT_SENTENCES_PER_CHUNK = 8
CHUNK_CONTEXT = (
    "Проверь все фактические утверждения: даты, числа, названия, цитаты, "
    "направление динамики."
)


def split_chunks(context: PipelineContext) -> PipelineState:
    """Нарезает context.extracted_text на чанки и сохраняет их в output/chunks/."""
    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)

    per_chunk = int(getattr(context.settings, "sentences_per_chunk", DEFAULT_SENTENCES_PER_CHUNK))
    logger.info("🔪 Нарезка текста на чанки (по %d предложений)", per_chunk)

    if not context.extracted_text:
        return PipelineState(success=False, error="Текст не извлечён", context=context)

    chunk_texts = split_into_chunks(context.extracted_text, sentences_per_chunk=per_chunk)
    if not chunk_texts:
        return PipelineState(success=False, error="Не удалось разбить текст на чанки", context=context)

    context.chunks = [
        Chunk(index=i, context=CHUNK_CONTEXT, text=text)
        for i, text in enumerate(chunk_texts, 1)
    ]
    logger.info("✅ Создано чанков: %d (ср. %d симв.)",
                len(context.chunks),
                context.text_length // max(1, len(context.chunks)))

    try:
        chunks_dir = context.settings.output_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for chunk in context.chunks:
            (chunks_dir / f"chunk_{chunk.index}.md").write_text(
                f"## Чанк {chunk.index}\n\nКонтекст: {chunk.context}\n\nТекст:\n{chunk.text}\n",
                encoding="utf-8",
            )
        logger.info("📁 Чанки сохранены в %s", chunks_dir)
    except Exception as e:
        logger.warning("⚠️ Не удалось сохранить чанки: %s", e)

    return PipelineState(context=context)