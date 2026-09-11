"""
Узел 1: Анализ входа.
Определяет тип входных данных и выбирает маршрут.
"""
import logging
from typing import List, Dict, Any

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.nodes.input_analysis")


def analyze_input(context: PipelineContext) -> PipelineState:
    """
    Анализирует входные документы и определяет тип данных.
    
    Возвращает:
        next_node: "image_or_table" если есть изображения/таблицы, иначе "text_only"
    """
    logger.info("📥 Анализ входных документов")
    
    docs = context.input_docs or []
    
    if not docs:
        # Загружаем документы из input_dir
        from agent.core.readers.input_reader import load_input_documents
        try:
            docs = load_input_documents(context.settings.input_dir)
            context.input_docs = docs
            logger.info(f"📁 Загружено документов: {len(docs)}")
        except Exception as e:
            return PipelineState(
                success=False,
                error=f"Не удалось загрузить документы: {e}"
            )
    
    if not docs:
        return PipelineState(
            success=False,
            error="Входные документы не найдены"
        )
    
    # Определяем типы документов
    has_image = False
    has_table = False
    has_text = False
    
    for doc in docs:
        kind = doc.get("kind", "text")
        if kind == "image":
            has_image = True
        elif kind == "table":
            has_table = True
        else:
            has_text = True
    
    # Извлекаем текст из текстовых документов сразу
    text_parts = []
    for doc in docs:
        kind = doc.get("kind", "text")
        content = doc.get("content", "")
        name = doc.get("name", "unknown")
        
        if kind == "text":
            text_parts.append(f"--- Текст: {name} ---\n{content}")
            has_text = True
    
    if text_parts:
        context.extracted_text = "\n\n".join(text_parts)
        context.text_length = len(context.extracted_text)
        logger.info(f"✍️ Извлечён текст: {context.text_length} симв.")
    
    # Определяем маршрут
    if has_image or has_table:
        logger.info("🖼️ Обнаружены изображения/таблицы → маршрут через text_extraction")
        return PipelineState(
            context=context,
            next_node="image_or_table"
        )
    else:
        logger.info("📄 Только текст → переход к length_check")
        return PipelineState(
            context=context,
            next_node="text_only"
        )
