"""
State graph для пайплайна v3.1.
Определяет узлы, рёбра и условные переходы графа состояний.
"""
import logging
from typing import Dict, Callable, Optional

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.graph")


class StateGraph:
    """
    Граф состояний пайплайна v3.1.
    
    Узлы:
        - input_analysis: анализ типа входа
        - text_extraction: извлечение текста (ридер)
        - length_check: проверка длины текста
        - chunking: нарезка на чанки
        - fact_extraction: извлечение фактов
        - fact_checking: проверка фактов чекерами
        - summarization: суммаризация отчётов
        - arbitration: арбитраж
        - report: генерация итогового отчёта
    
    Рёбра определяются динамически на основе результатов узлов.
    """
    
    def __init__(self):
        self.nodes: Dict[str, Callable[[PipelineContext], PipelineState]] = {}
        self.edges: Dict[str, str] = {}  # node -> next_node
        self.conditional_edges: Dict[str, Dict[str, str]] = {}  # node -> {condition: next_node}
        
        self._register_nodes()
    
    def _register_nodes(self):
        """Регистрирует все узлы графа."""
        from agent.state.nodes.input_analysis import analyze_input
        from agent.state.nodes.text_extraction import extract_text
        from agent.state.nodes.length_check import check_length
        from agent.state.nodes.chunking import split_chunks
        from agent.state.nodes.fact_extraction import extract_facts
        from agent.state.nodes.fact_checking import run_fact_checking
        from agent.state.nodes.summarization import summarize_reports
        from agent.state.nodes.arbitration import run_arbitration
        from agent.state.nodes.report import generate_report
        
        self.nodes = {
            "input_analysis": analyze_input,
            "text_extraction": extract_text,
            "length_check": check_length,
            "chunking": split_chunks,
            "fact_extraction": extract_facts,
            "fact_checking": run_fact_checking,
            "summarization": summarize_reports,
            "arbitration": run_arbitration,
            "report": generate_report,
        }
        
        # Базовые рёбра (линейный поток по умолчанию)
        self.edges = {
            "input_analysis": "text_extraction",  # Условное: может перейти сразу к length_check
            "text_extraction": "length_check",
            "length_check": None,  # Условное: chunking или fact_extraction
            "chunking": "fact_extraction",
            "fact_extraction": "fact_checking",
            "fact_checking": "summarization",
            "summarization": "arbitration",
            "arbitration": "report",
            "report": None,  # Конец
        }
        
        # Условные переходы
        self.conditional_edges = {
            "input_analysis": {
                "image_or_table": "text_extraction",
                "text_only": "length_check",
            },
            "length_check": {
                "needs_chunking": "chunking",
                "no_chunking_needed": "fact_extraction",
            },
        }
    
    def get_next_node(self, current_node: str, condition: Optional[str] = None) -> Optional[str]:
        """
        Определяет следующий узел на основе текущего и условия.
        
        Args:
            current_node: текущий узел
            condition: условие перехода (если есть)
        
        Returns:
            Имя следующего узла или None (конец графа)
        """
        if current_node not in self.nodes:
            logger.error(f"❌ Неизвестный узел: {current_node}")
            return None
        
        # Проверяем условные переходы
        if current_node in self.conditional_edges and condition:
            conditional = self.conditional_edges[current_node]
            if condition in conditional:
                return conditional[condition]
        
        # Возвращаем базовое ребро
        return self.edges.get(current_node)
    
    def execute(self, context: PipelineContext) -> PipelineState:
        """
        Выполняет граф состояний.
        
        Args:
            context: начальный контекст пайплайна
        
        Returns:
            Финальное состояние после выполнения всех узлов
        """
        current_node = "input_analysis"
        state = PipelineState(context=context)
        
        logger.info("🚀 Запуск графа состояний v3.1")
        
        while current_node is not None:
            context.add_history(current_node)
            logger.info(f"📍 Узел: {current_node}")
            
            if current_node not in self.nodes:
                state.error = f"Неизвестный узел: {current_node}"
                state.success = False
                break
            
            try:
                node_func = self.nodes[current_node]
                state = node_func(context)
                
                if not state.success:
                    logger.error(f"❌ Ошибка в узле {current_node}: {state.error}")
                    break
                
                # Определяем следующий узел
                next_node = self.get_next_node(current_node, state.next_node)
                
                if next_node is None and current_node != "report":
                    # Конец графа
                    break
                
                current_node = next_node or current_node
                
            except Exception as e:
                logger.exception(f"❌ Исключение в узле {current_node}: {e}")
                state.success = False
                state.error = str(e)
                break
        
        logger.info(f"{'✅' if state.success else '❌'} Граф завершён")
        return state
