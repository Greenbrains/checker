"""
Узел 2: Извлечение текста.
Субагент-ридер для изображений и таблиц.
"""
import logging

from agent.state.context import PipelineContext, PipelineState
from agent.subagents.factory import SubagentFactory

logger = logging.getLogger("agent.state.nodes.text_extraction")


def extract_text(context: PipelineContext) -> PipelineState:
    """
    Извлекает текст из изображений и таблиц с помощью субагента-ридера.
    
    Для текстовых документов текст уже извлечён в analyze_input.
    """
    logger.info("📖 Извлечение текста из изображений/таблиц")
    
    # Находим документы, требующие обработки
    docs_to_process = []
    for doc in context.input_docs:
        kind = doc.get("kind", "text")
        if kind in ("image", "table"):
            docs_to_process.append(doc)
    
    if not docs_to_process:
        logger.info("✅ Нет изображений/таблиц для обработки")
        return PipelineState(context=context)
    
    try:
        # Создаём ридера через фабрику
        factory = SubagentFactory(
            client=None,  # Будет передан позже
            settings=context.settings,
            registry=None  # Будет передан позже
        )
        
        # Для простоты: используем программное извлечение
        # В полной версии здесь будет вызов ReaderAgent
        text_parts = [context.extracted_text] if context.extracted_text else []
        
        for doc in docs_to_process:
            kind = doc.get("kind")
            content = doc.get("content", "")
            name = doc.get("name", "unknown")
            
            if kind == "image":
                # Пока заглушка — в полной версии ReaderAgent
                text_parts.append(f"[Изображение: {name} — требует распознавания]")
                logger.warning(f"⚠️ Изображение {name}: требуется ReaderAgent (OCR)")
            elif kind == "table":
                text_parts.append(f"--- Таблица: {name} ---\n{content}")
                logger.info(f"📊 Таблица {name}: добавлена")
        
        context.extracted_text = "\n\n".join(text_parts)
        context.text_length = len(context.extracted_text)
        
        logger.info(f"✅ Текст извлечён: {context.text_length} симв.")
        
        return PipelineState(context=context)
        
    except Exception as e:
        logger.exception(f"❌ Ошибка извлечения текста: {e}")
        return PipelineState(
            success=False,
            error=f"Не удалось извлечь текст: {e}"
        )
