"""
State graph пайплайна v3.1.
Version: 3.2.0
Изменения 3.2.0:
    - ИСПРАВЛЕН бесконечный цикл на терминальном узле: `current = next or current`
      при next=None оставлял узел тем же (report крутился вечно);
    - защита от зацикливания: max_steps + лимит посещений узла;
    - next_node может быть как условием, так и прямым именем узла;
    - несуществующие рёбра логируются и откатываются к базовому ребру;
    - возвращаемый PipelineState всегда несёт context.
"""
import logging
from collections import Counter
from typing import Callable, Dict, Optional

from agent.state.context import PipelineContext, PipelineState

logger = logging.getLogger("agent.state.graph")


class StateGraph:
    """Граф состояний пайплайна v3.1 (узлы + условные рёбра)."""

    def __init__(self, max_steps: int = 50, max_node_visits: int = 3, entry_node: str = "input_analysis"):
        self.nodes: Dict[str, Callable[[PipelineContext], PipelineState]] = {}
        self.edges: Dict[str, Optional[str]] = {}
        self.conditional_edges: Dict[str, Dict[str, str]] = {}
        self.max_steps = max_steps
        self.max_node_visits = max_node_visits
        self.entry_node = entry_node
        self._register_nodes()

    def _register_nodes(self) -> None:
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

        # Базовые (линейные) рёбра
        self.edges = {
            "input_analysis": "text_extraction",
            "text_extraction": "length_check",
            "length_check": None,          # решается условным ребром
            "chunking": "fact_extraction",
            "fact_extraction": "fact_checking",
            "fact_checking": "summarization",
            "summarization": "arbitration",
            "arbitration": "report",
            "report": None,                # конец графа
        }

        # Условные переходы: имя условия → узел
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

    # ---------- резолв перехода ----------

    def _base_next(self, node: str) -> Optional[str]:
        nxt = self.edges.get(node)
        if nxt is not None and nxt not in self.nodes:
            logger.error("❌ Узел %s ссылается на несуществующий узел %r", node, nxt)
            return None
        return nxt

    def get_next_node(self, current_node: str, condition: Optional[str] = None) -> Optional[str]:
        """Следующий узел: сначала условие, потом базовое ребро. None — конец графа."""
        if current_node not in self.nodes:
            logger.error("❌ Неизвестный узел: %s", current_node)
            return None

        if condition:
            # Разрешаем узлу вернуть сразу имя узла
            if condition in self.nodes:
                return condition
            table = self.conditional_edges.get(current_node)
            if table:
                target = table.get(condition)
                if target:
                    if target in self.nodes:
                        return target
                    logger.error("❌ Условие %r узла %s ведёт в несуществующий узел %r",
                                 condition, current_node, target)
                    return self._base_next(current_node)
            logger.warning("⚠️ Узел %s: неизвестное условие %r — беру базовое ребро",
                           current_node, condition)

        return self._base_next(current_node)

    # ---------- выполнение ----------

    def execute(self, context: PipelineContext) -> PipelineState:
        """Прогоняет контекст по графу до конца (или до ошибки/лимита)."""
        current_node: Optional[str] = self.entry_node
        state = PipelineState(context=context)
        visits: Counter = Counter()
        steps = 0

        logger.info("🚀 Запуск графа состояний v3.1")

        while current_node is not None:
            steps += 1
            if steps > self.max_steps:
                state.success = False
                state.error = f"Превышен лимит шагов графа ({self.max_steps})"
                logger.error("❌ %s", state.error)
                break

            visits[current_node] += 1
            if visits[current_node] > self.max_node_visits:
                state.success = False
                state.error = f"Узел {current_node} посещён более {self.max_node_visits} раз — петля"
                logger.error("❌ %s", state.error)
                break

            context.add_history(current_node)
            logger.info("📍 Узел: %s (шаг %d)", current_node, steps)

            node_func = self.nodes.get(current_node)
            if node_func is None:
                state.success = False
                state.error = f"Неизвестный узел: {current_node}"
                logger.error("❌ %s", state.error)
                break

            try:
                result = node_func(context)
                state = result if result is not None else PipelineState(context=context)
                if state.context is None:
                    state.context = context
            except Exception as e:
                logger.exception("❌ Исключение в узле %s: %s", current_node, e)
                state.success = False
                state.error = str(e)
                state.context = context
                break

            if not state.success:
                logger.error("❌ Ошибка в узле %s: %s", current_node, state.error)
                break

            next_node = self.get_next_node(current_node, state.next_node)
            if next_node is None:
                logger.info("🏁 Конец графа (узел %s)", current_node)
                break
            current_node = next_node

        state.context = state.context or context
        logger.info("%s Граф завершён: %s", "✅" if state.success else "❌",
                    " → ".join(context.node_path))
        return state