"""
Утилиты для обработки текста.
Используются узлами графа состояний.
"""
import re
import hashlib
from typing import List


def normalize_text(text: str) -> str:
    """Нормализует текст: убирает лишние пробелы, приводит к единому формату."""
    if not text:
        return ""
    # Заменяем множественные пробелы на один
    text = re.sub(r'\s+', ' ', text)
    # Убираем пробелы вокруг знаков препинания
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    return text.strip()


def split_sentences(text: str) -> List[str]:
    """Разбивает текст на предложения."""
    if not text:
        return []
    # Разбиваем по точкам, восклицательным и вопросительным знакам
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences


def split_into_chunks(text: str, sentences_per_chunk: int = 8) -> List[str]:
    """
    Разбивает текст на чанки по предложениям.
    sentences_per_chunk: количество предложений в чанке (7-10).
    """
    sentences = split_sentences(text)

    chunks = []
    for i in range(0, len(sentences), sentences_per_chunk):
        chunk_sentences = sentences[i:i + sentences_per_chunk]
        chunk_text = ' '.join(chunk_sentences)
        if chunk_text.strip():
            chunks.append(chunk_text)

    return chunks


def extract_urls(text: str) -> List[str]:
    """Извлекает все URL из текста."""
    if not text:
        return []
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    return list(set(urls))  # Убираем дубликаты


def report_broken(report: str) -> bool:
    """Проверяет, является ли отчёт 'сломанным' (невалидным)."""
    from agent.base import MIN_REPORT_CHARS

    r = (report or "").strip().lower()
    if len(r) < MIN_REPORT_CHARS:
        return True
    if "не удалось найти файл" in r or "предоставьте текст" in r:
        return True
    return not any(m in r for m in ("ошибк", "отчёт", "тезис"))


def fact_id(text: str, chunk_index: int) -> str:
    """Генерирует уникальный ID для факта на основе текста и индекса чанка."""
    # Хешируем текст для создания уникального ID
    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
    return f"f{chunk_index}_{text_hash}"
