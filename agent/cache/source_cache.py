"""
SourceCache — общий кеш источников для чекеров.

Хранит:
    - Нормализованный запрос → результаты поиска
    - URL → текст сниппета

Преимущества:
    - Чекеры не ищут повторно одни и те же источники
    - Экономия итераций и токенов
"""
import logging
import time
from typing import Dict, List, Optional, Any

logger = logging.getLogger("agent.cache.source_cache")


class SourceCache:
    """Кеш источников для общего доступа между чекерами."""
    
    def __init__(self, ttl_seconds: int = 3600):
        """
        Args:
            ttl_seconds: время жизни записи в кеше (по умолчанию 1 час)
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds
        logger.info(f"🗄️ SourceCache инициализирован (TTL={ttl_seconds}s)")
    
    def _normalize_query(self, query: str) -> str:
        """Нормализует поисковый запрос для ключа кеша."""
        return query.lower().strip()
    
    def get(self, query: str) -> Optional[List[Dict]]:
        """
        Получает результаты поиска из кеша.
        
        Args:
            query: поисковый запрос
        
        Returns:
            Результаты поиска или None (если нет в кеше или истёк TTL)
        """
        key = self._normalize_query(query)
        
        if key not in self._cache:
            return None
        
        entry = self._cache[key]
        
        # Проверяем TTL
        if time.time() - entry["timestamp"] > self._ttl:
            logger.debug(f"🕐 Истёк TTL для запроса: {query[:50]}...")
            del self._cache[key]
            return None
        
        logger.debug(f"✅ Cache hit для: {query[:50]}...")
        return entry["results"]
    
    def set(self, query: str, results: List[Dict]):
        """
        Сохраняет результаты поиска в кеш.
        
        Args:
            query: поисковый запрос
            results: результаты поиска
        """
        key = self._normalize_query(query)
        self._cache[key] = {
            "results": results,
            "timestamp": time.time(),
        }
        logger.debug(f"💾 Cache set для: {query[:50]}... ({len(results)} результатов)")
    
    def get_url(self, url: str) -> Optional[str]:
        """
        Получает текст страницы по URL из кеша.
        
        Args:
            url: URL страницы
        
        Returns:
            Текст страницы или None
        """
        return self._cache.get(url, {}).get("text")
    
    def set_url(self, url: str, text: str):
        """
        Сохраняет текст страницы по URL в кеш.
        
        Args:
            url: URL страницы
            text: текст страницы
        """
        self._cache[url] = {
            "text": text,
            "timestamp": time.time(),
        }
        logger.debug(f"💾 Cache set URL: {url[:50]}... ({len(text)} симв.)")
    
    def stats(self) -> Dict[str, int]:
        """Возвращает статистику кеша."""
        return {
            "entries": len(self._cache),
        }
    
    def clear(self):
        """Очищает кеш."""
        self._cache.clear()
        logger.info("🧹 SourceCache очищен")
