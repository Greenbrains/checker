"""
Узел 5: Извлечение фактов.
Извлекает факты из чанков, дедуплицирует и распределяет по чекерам.
"""
import logging
import hashlib

from agent.state.context import PipelineContext, PipelineState, Fact

logger = logging.getLogger("agent.state.nodes.fact_extraction")


def extract_facts_from_chunk(chunk_text: str, chunk_index: int) -> list:
    """
    Программно извлекает факты из текста чанка.
    
    Это упрощённая версия — в полной версии будет LLM-субагент.
    
    Извлекает:
        - Даты (YYYY, DD.MM.YYYY)
        - Числа (проценты, суммы)
        - Названия (организации, имена)
        - Утверждения с кванторами ("все", "никогда", "впервые")
    """
    facts = []
    sentences = chunk_text.split('.')
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or len(sentence) < 20:
            continue
        
        # Определяем тип факта
        fact_type = "claim"
        priority = "normal"
        
        # Проверяем на наличие дат
        import re
        if re.search(r'\b\d{4}\b', sentence) or re.search(r'\d{2}\.\d{2}\.\d{4}', sentence):
            fact_type = "date"
            priority = "critical"
        
        # Проверяем на наличие чисел/процентов
        if re.search(r'\d+[%\s]', sentence) or re.search(r'\d+\s*(млн|млрд|тыс)', sentence, re.IGNORECASE):
            fact_type = "number"
            priority = "critical"
        
        # Проверяем на названия организаций
        if re.search(r'(ООО|АО|ЗАО|ПАО|Inc\.|Ltd\.|GmbH|S\.A\.)', sentence):
            fact_type = "name"
            priority = "critical"
        
        # Создаём факт
        fact_id = hashlib.md5(f"{chunk_index}:{sentence[:50]}".encode()).hexdigest()[:12]
        
        facts.append(Fact(
            id=fact_id,
            text=sentence + '.',
            chunk_index=chunk_index,
            priority=priority,
            fact_type=fact_type
        ))
    
    return facts


def distribute_facts_to_checkers(facts: list, num_checkers: int) -> dict:
    """
    Распределяет факты по чекерам.
    
    Стратегия:
        1. Критичные факты равномерно распределяются
        2. Обычные факты дополняют распределение
    
    Returns:
        Dict[int, List[str]]: {checker_idx: [fact_ids]}
    """
    distribution = {i: [] for i in range(1, num_checkers + 1)}
    
    # Сначала критичные факты
    critical_facts = [f for f in facts if f.priority == "critical"]
    normal_facts = [f for f in facts if f.priority == "normal"]
    
    # Равномерно распределяем критичные
    for i, fact in enumerate(critical_facts):
        checker_idx = (i % num_checkers) + 1
        distribution[checker_idx].append(fact.id)
    
    # Равномерно распределяем обычные
    for i, fact in enumerate(normal_facts):
        checker_idx = (i % num_checkers) + 1
        distribution[checker_idx].append(fact.id)
    
    return distribution


def extract_facts(context: PipelineContext) -> PipelineState:
    """
    Извлекает факты из всех чанков, дедуплицирует и распределяет по чекерам.
    """
    logger.info("🎯 Извлечение фактов из чанков")
    
    if not context.chunks:
        return PipelineState(
            success=False,
            error="Чанки не созданы"
        )
    
    all_facts = []
    
    # Извлекаем факты из каждого чанка
    for chunk in context.chunks:
        chunk_facts = extract_facts_from_chunk(chunk.text, chunk.index)
        all_facts.extend(chunk_facts)
        logger.debug(f"Чанк {chunk.index}: извлечено {len(chunk_facts)} фактов")
    
    # Дедупликация фактов по тексту
    seen_texts = set()
    unique_facts = []
    for fact in all_facts:
        normalized = fact.text.lower().strip()
        if normalized not in seen_texts:
            seen_texts.add(normalized)
            unique_facts.append(fact)
    
    duplicate_count = len(all_facts) - len(unique_facts)
    if duplicate_count > 0:
        logger.info(f"🔄 Дедупликация: удалено {duplicate_count} дубликатов")
    
    context.facts = unique_facts
    logger.info(f"✅ Извлечено фактов: {len(context.facts)}")
    
    # Распределяем факты по чекерам
    num_checkers = len(context.settings.checker_model_list)
    distribution = distribute_facts_to_checkers(unique_facts, num_checkers)
    context.facts_by_checker = distribution
    
    for checker_idx, fact_ids in distribution.items():
        logger.info(f"Чекер {checker_idx}: {len(fact_ids)} фактов")
    
    # Логгируем факты для отладки
    for fact in unique_facts:
        logger.debug(f"  [{fact.id}] {fact.fact_type}/{fact.priority}: {fact.text[:60]}...")
    
    return PipelineState(context=context)
