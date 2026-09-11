"""
Узел 5: Извлечение фактов (программно, без LLM), дедупликация и раскладка по чекерам.
Version: 2.1.0
Изменения 2.1.0:
    - ИСПРАВЛЕНО: факты больше не режутся по text.split('.') — используются границы предложений;
    - тип факта определяется точечными регулярками (проценты/единицы/организации/цитаты),
      а не «любое число с пробелом»;
    - период не дублируется в конце предложения;
    - заполняется Chunk.facts, есть лимит фактов на чекера.
"""
import logging
import re
from typing import Dict, List

from agent.state.context import Fact, PipelineContext, PipelineState
from agent.state.text_utils import fact_id, normalize_text, split_sentences

logger = logging.getLogger("agent.state.nodes.fact_extraction")

MIN_FACT_CHARS = 25

_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|(?:19|20)\d{2}\b"
    r"|\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r")",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"\d[\d\s\u00a0.,]*\s*(?:%|процент\w*|млн|млрд|тыс\w*|трлн|₽|\$|€|руб\w*|долл\w*|евро)"
    r"|\b\d{1,3}(?:[ \u00a0]\d{3})+\b"
    r"|\b\d+[.,]\d+\b",
    re.IGNORECASE,
)
_ORG_RE = re.compile(
    r"\b(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|НКО|Inc\.?|Ltd\.?|LLC|GmbH|S\.A\.|Corp\.?|Co\.)\b"
)
_QUOTE_RE = re.compile(r"«[^»]{5,}»|“[^”]{5,}”|\"[^\"]{5,}\"")
_CLAIM_RE = re.compile(
    r"\b(?:впервые|единственн\w*|рекордн\w*|никогда|сам(?:ый|ая|ое|ые)|"
    r"наибольш\w*|наименьш\w*|крупнейш\w*|обогнал\w*|превысил\w*|"
    r"выросл\w*|снизил\w*|увеличил\w*|сократил\w*)\b",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^[#>*\-\s]+$|^\*\*[^*]{1,60}\*\*$")


def extract_facts_from_chunk(chunk_text: str, chunk_index: int) -> List[Fact]:
    """Программное извлечение проверяемых утверждений из чанка."""
    facts: List[Fact] = []
    for sentence in split_sentences(chunk_text):
        sentence = sentence.strip()
        if len(sentence) < MIN_FACT_CHARS or _HEADING_RE.match(sentence):
            continue
        if not re.search(r"[A-Za-zА-Яа-яЁё]", sentence):
            continue

        fact_type, priority = "claim", "normal"
        if _DATE_RE.search(sentence):
            fact_type, priority = "date", "critical"
        if _NUMBER_RE.search(sentence):
            fact_type, priority = "number", "critical"
        if _ORG_RE.search(sentence):
            fact_type, priority = "name", "critical"
        if _QUOTE_RE.search(sentence):
            fact_type, priority = "quote", "critical"
        if fact_type == "claim" and _CLAIM_RE.search(sentence):
            priority = "critical"  # кванторные утверждения проверяемы по первоисточнику

        text = sentence if sentence.endswith((".", "!", "?", "…", "»", '"')) else sentence + "."
        facts.append(Fact(
            id=fact_id(text, chunk_index),
            text=text,
            chunk_index=chunk_index,
            priority=priority,
            fact_type=fact_type,
        ))
    return facts


def distribute_facts_to_checkers(facts: List[Fact], num_checkers: int,
                                 max_per_checker: int = 40) -> Dict[int, List[str]]:
    """Равномерная раскладка: критичные идут первыми, каждый чекер получает свою долю."""
    if num_checkers <= 0:
        num_checkers = 1
    
    # Если лимит 0 или отрицательный - используем разумный дефолт
    if max_per_checker <= 0:
        max_per_checker = 40
    
    distribution: Dict[int, List[str]] = {i: [] for i in range(1, num_checkers + 1)}
    
    ordered = [f for f in facts if f.priority == "critical"] + [f for f in facts if f.priority != "critical"]
    limit = max_per_checker * num_checkers
    if len(ordered) > limit:
        logger.warning("⚠️ Фактов %d > лимита %d — лишние (низкоприоритетные) отброшены",
                       len(ordered), limit)
        ordered = ordered[:limit]

    for i, fact in enumerate(ordered):
        distribution[(i % num_checkers) + 1].append(fact.id)
    return distribution


def extract_facts(context: PipelineContext) -> PipelineState:
    """Извлекает, дедуплицирует факты и распределяет их по чекерам."""
    logger.info("🎯 Извлечение фактов из чанков")

    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)
    if not context.chunks:
        return PipelineState(success=False, error="Чанки не созданы", context=context)

    all_facts: List[Fact] = []
    for chunk in context.chunks:
        chunk.facts = extract_facts_from_chunk(normalize_text(chunk.text), chunk.index)
        all_facts.extend(chunk.facts)
        logger.debug("Чанк %d: %d фактов", chunk.index, len(chunk.facts))

    seen, unique = set(), []
    for fact in all_facts:
        key = re.sub(r"\s+", " ", fact.text).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(fact)

    dropped = len(all_facts) - len(unique)
    if dropped:
        logger.info("🔄 Дедупликация: отброшено %d дубликатов", dropped)

    context.facts = unique
    critical = sum(1 for f in unique if f.priority == "critical")
    logger.info("✅ Извлечено фактов: %d (критичных: %d)", len(unique), critical)

    num_checkers = len(context.settings.checker_model_list)
    max_per = int(getattr(context.settings, "max_facts_per_checker", 40))
    context.facts_by_checker = distribute_facts_to_checkers(unique, num_checkers, max_per)

    for idx, ids in sorted(context.facts_by_checker.items()):
        model = context.settings.checker_model_list[idx - 1] if idx <= num_checkers else "?"
        logger.info("Чекер %d (%s): %d фактов", idx, model, len(ids))

    context.metadata["facts_stats"] = {
        "total": len(unique),
        "critical": critical,
        "dropped_duplicates": dropped,
    }
    return PipelineState(context=context)