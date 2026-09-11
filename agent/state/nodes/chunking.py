"""
Узел 4: Нарезка на чанки.
Программная нарезка текста (без LLM).
"""
import logging
import re

from agent.state.context import PipelineContext, PipelineState, Chunk

logger = logging.getLogger("agent.state.nodes.chunking")

# Параметры нарезки
DEFAULT_SENTENCES_PER_CHUNK = 8  # 7-10 предложений


def split_into_chunks(text: str, sentences_per_chunk: int = DEFAULT_SENTENCES_PER_CHUNK) -> list:
    """
    Разбивает текст на чанки по предложениям.
    
    Args:
        text: текст для разбивки
        sentences_per_chunk: количество предложений в чанке
    
    Returns:
        список чанков (строки)
    """
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


def split_chunks(context: PipelineContext) -> PipelineState:
    """
    Нарезает текст на чанки программно.
    
    Без использования LLM — только программная логика.
    """
    logger.info(f"🔪 Нарезка текста на чанки (по {DEFAULT_SENTENCES_PER_CHUNK} предложений)")
    
    if not context.extracted_text:
        return PipelineState(
            success=False,
            error="Текст не извлечён"
        )
    
    # Нарезаем текст
    chunk_texts = split_into_chunks(
        context.extracted_text,
        sentences_per_chunk=DEFAULT_SENTENCES_PER_CHUNK
    )
    
    if not chunk_texts:
        return PipelineState(
            success=False,
            error="Не удалось разбить текст на чанки"
        )
    
    # Создаём объекты Chunk
    context.chunks = []
    for i, text in enumerate(chunk_texts, 1):
        chunk = Chunk(
            index=i,
            context="Проверь все фактические утверждения: даты, числа, названия, цитаты, направления динамики.",
            text=text
        )
        context.chunks.append(chunk)
    
    logger.info(f"✅ Создано чанков: {len(context.chunks)}")
    
    # Сохраняем чанки в файлы для отладки
    try:
        chunks_dir = context.settings.output_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)
        
        for chunk in context.chunks:
            chunk_file = chunks_dir / f"chunk_{chunk.index}.md"
            chunk_file.write_text(
                f"## Чанк {chunk.index}\n\nКонтекст: {chunk.context}\n\nТекст:\n{chunk.text}\n",
                encoding="utf-8"
            )
        
        logger.info(f"📁 Чанки сохранены в {chunks_dir}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось сохранить чанки: {e}")
    
    return PipelineState(context=context)
