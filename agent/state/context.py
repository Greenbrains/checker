"""
PipelineContext — единое состояние пайплайна v3.1.
Передаётся между узлами графа, содержит все данные и метрики.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from config.settings import Settings
from agent.base import UsageTracker


@dataclass
class Fact:
    """Извлечённый факт для проверки."""
    id: str  # уникальный ID факта
    text: str  # текст факта
    chunk_index: int  # индекс чанка, откуда извлечён
    priority: str = "normal"  # "critical" или "normal"
    fact_type: str = "claim"  # "date", "number", "name", "quote", "claim"
    sources: List[str] = field(default_factory=list)  # найденные источники
    verified: bool = False  # проверен ли факт
    verification_result: Optional[str] = None  # результат проверки
    
    def prompt_line(self) -> str:
        """Формирует строку для промпта чекера."""
        marker = "‼️" if self.priority == "critical" else "•"
        return f"[{self.id}] {marker} {self.text} (тип: {self.fact_type})"


@dataclass
class Chunk:
    """Чанк текста для проверки."""
    index: int
    context: str
    text: str
    facts: List[Fact] = field(default_factory=list)  # факты, извлечённые из чанка


@dataclass
class CheckerReport:
    """Отчёт чекера по набору фактов."""
    checker_idx: int
    model: str
    facts_checked: List[str]  # ID проверенных фактов
    report: str
    sources: List[str] = field(default_factory=list)
    iteration_count: int = 0
    tokens_used: int = 0


@dataclass
class PipelineContext:
    """
    Единый контекст пайплайна v3.1.
    
    Атрибуты:
        input_docs: список входных документов
        extracted_text: извлечённый текст (после ридера)
        text_length: длина текста
        chunks: список чанков
        facts: список всех извлечённых фактов (дедуплицированных)
        facts_by_checker: распределение фактов по чекерам {checker_idx: [fact_ids]}
        checker_reports: отчёты чекеров
        source_cache: общий кеш источников
        usage: UsageTracker
        settings: настройки
        current_node: текущий узел графа
        history: лог переходов между узлами [(node_name, timestamp), ...]
        metadata: дополнительные метаданные
    """
    # Входные данные
    input_docs: List[Dict[str, Any]] = field(default_factory=list)
    extracted_text: str = ""
    text_length: int = 0
    
    # Чанки и факты
    chunks: List[Chunk] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)
    facts_by_checker: Dict[int, List[str]] = field(default_factory=dict)
    
    # Отчёты
    checker_reports: List[CheckerReport] = field(default_factory=list)
    summarized_reports: str = ""
    final_report: str = ""
    
    # Кеш и метрики
    source_cache: Dict[str, Any] = field(default_factory=dict)
    usage: UsageTracker = field(default_factory=UsageTracker)
    settings: Optional[Settings] = None
    
    # Состояние графа
    current_node: str = "START"
    history: List[tuple] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_history(self, node_name: str):
        """Добавляет запись в историю переходов."""
        import time
        self.history.append((node_name, time.time()))
        self.current_node = node_name
    
    @property
    def node_path(self) -> List[str]:
        """Возвращает список пройденных узлов."""
        return [h[0] for h in self.history]
    
    def cache_get(self, key: str) -> Optional[Any]:
        """Получает значение из кеша."""
        normalized_key = key.lower().strip()
        cached = self.source_cache.get(normalized_key)
        if cached:
            return cached.get("results") or cached.get("value")
        return None
    
    def cache_put(self, key: str, value: Any):
        """Сохраняет значение в кеш."""
        normalized_key = key.lower().strip()
        self.source_cache[normalized_key] = {
            "results": value,
            "timestamp": time.time()
        }
    
    def get_facts_for_checker(self, checker_idx: int) -> List[Fact]:
        """Возвращает факты, назначенные чекеру."""
        fact_ids = self.facts_by_checker.get(checker_idx, [])
        return [f for f in self.facts if f.id in fact_ids]
    
    def add_source_to_cache(self, query: str, results: List[Dict]):
        """Добавляет результаты поиска в кеш."""
        normalized_query = query.lower().strip()
        self.source_cache[normalized_query] = {
            "results": results,
            "timestamp": time.time()
        }
    
    def get_from_source_cache(self, query: str) -> Optional[List[Dict]]:
        """Получает результаты поиска из кеша."""
        normalized_query = query.lower().strip()
        cached = self.source_cache.get(normalized_query)
        if cached:
            return cached["results"]
        return None


@dataclass
class PipelineState:
    """
    Результат выполнения узла графа.
    
    Атрибуты:
        success: успешно ли выполнен узел
        context: обновлённый контекст
        error: сообщение об ошибке (если есть)
        next_node: следующий узел для перехода (если условный)
    """
    success: bool = True
    context: Optional[PipelineContext] = None
    error: Optional[str] = None
    next_node: Optional[str] = None
