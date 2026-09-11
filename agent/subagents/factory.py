"""
SubagentFactory — фабрика для создания субагентов пайплайна v3.1.

Инкапсулирует:
    - Выбор модели
    - Загрузку скилла через load_skill()
    - Сборку системного промпта через PromptLoader
    - Подключение инструментов через ToolRegistry
    - Создание BaseAgent с нужными параметрами

Оркестратор не знает деталей создания агентов — он запрашивает у фабрики.
"""
import logging
from typing import Optional, Dict, Any

from agent.base import BaseAgent, UsageTracker
from agent.core.prompts.loader import PromptLoader
from agent.core.tools.agent_tools import load_skill
from agent.core.tools.registry import ToolRegistry

logger = logging.getLogger("agent.subagents.factory")


class SubagentFactory:
    """Фабрика субагентов для пайплайна v3.1."""
    
    # Конфигурация субагентов по умолчанию
    DEFAULT_CONFIGS = {
        "reader": {
            "skill": "image-reading",
            "tools": [],  # Ридеру инструменты не нужны (или code_execute для таблиц)
            "temperature": 0.1,
            "max_tokens": 8192,
        },
        "chunker": {
            "skill": None,  # Программная нарезка, без LLM
            "tools": [],
            "temperature": 0.1,
            "max_tokens": 4096,
        },
        "checker": {
            "skill": "fact-checking",
            "tools": ["web_search", "code_execute"],
            "temperature": 0.1,
            "max_tokens": 16384,
            "max_iterations": 6,  # Увеличенный бюджет
        },
        "arbiter": {
            "skill": "fact-arbitration",
            "tools": [],  # Арбитр работает с готовыми отчётами
            "temperature": 0.1,
            "max_tokens": 16384,
            "max_iterations": 3,
        },
    }
    
    def __init__(self, client, settings, registry: ToolRegistry):
        self.client = client
        self.settings = settings
        self.registry = registry
        self.prompt_loader = PromptLoader()
        
        logger.info(f"🏭 SubagentFactory инициализирован")
    
    def _get_system_prompt(self, skill_name: Optional[str]) -> str:
        """Собирает системный промпт с навыком или без."""
        if skill_name:
            skill_context = load_skill(skill_name)
            return self.prompt_loader.render_system_prompt(skill_context)
        else:
            # Базовый промпт без навыка
            return self.prompt_loader.data.get("system_prompt", "Ты — ассистент.")
    
    def _get_tools(self, tool_names: list):
        """Получает инструменты по именам."""
        if not tool_names:
            return [], {}
        return self.registry.get_tools_by_names(set(tool_names))
    
    def create_agent(
        self,
        role_name: str,
        model: str,
        skill: Optional[str] = None,
        tools: Optional[list] = None,
        temperature: float = 0.3,
        max_tokens: int = 16384,
        usage_tracker: Optional[UsageTracker] = None,
    ) -> BaseAgent:
        """
        Создаёт базового агента с заданными параметрами.
        
        Args:
            role_name: имя роли (для логов)
            model: модель
            skill: имя навыка (или None)
            tools: список имён инструментов
            temperature: температура генерации
            max_tokens: лимит токенов
            usage_tracker: трекер использования (общий для пайплайна)
        """
        system_prompt = self._get_system_prompt(skill)
        tools_schema, tool_router = self._get_tools(tools or [])
        
        logger.debug(f"🔧 Создание агента {role_name}: model={model}, skill={skill}, tools={tools}")
        
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=system_prompt,
            tools_schema=tools_schema,
            tool_router=tool_router,
            usage_tracker=usage_tracker,
            role_name=role_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    def create_reader(self, model: Optional[str] = None, for_tables: bool = False) -> BaseAgent:
        """
        Создаёт ReaderAgent для извлечения текста из изображений/таблиц.
        
        Args:
            model: модель (по умолчанию model_agent)
            for_tables: True для таблиц (добавляет code_execute)
        """
        config = self.DEFAULT_CONFIGS["reader"]
        model = model or self.settings.model_agent
        
        tools = config["tools"].copy()
        if for_tables:
            tools.append("code_execute")
        
        # Выбираем скилл в зависимости от типа документа
        skill = "table-parsing" if for_tables else "image-reading"
        
        return self.create_agent(
            role_name="reader",
            model=model,
            skill=skill,
            tools=tools,
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
        )
    
    def create_checker(
        self,
        idx: int,
        model: str,
        skill: str = "fact-checking",
        max_iterations: Optional[int] = None,
    ) -> BaseAgent:
        """
        Создаёт CheckerAgent для проверки фактов.
        
        Args:
            idx: индекс чекера (для логов)
            model: модель из checker_model_list
            skill: имя навыка
            max_iterations: бюджет итераций (по умолчанию из конфига)
        """
        config = self.DEFAULT_CONFIGS["checker"]
        max_iter = max_iterations or config["max_iterations"]
        
        # Добавляем addendum к промпту
        addendum = f"""
РЕЖИМ ЧЕКЕРА v3.1 — ОПТИМИЗИРОВАННЫЙ

Ты проверяешь факты в предоставленном наборе.

Правила:
1. Приоритизируй критичные факты (даты, числа, названия, цитаты).
2. На каждый факт трать НЕ БОЛЕЕ 1-2 вызовов web_search.
3. Бюджет: {max_iter} итераций на набор фактов.
4. Используй общий кеш источников (context.source_cache).
5. Не повторяй одинаковые запросы.

ФОРМАТ ОТЧЁТА:
# Отчёт чекера {idx}

## 1. Проверенные факты
[факт, результат, источники]

## 2. Ошибки
[№ факта, цитата, тип ошибки, корректное значение, URL]

## 3. Неподтверждённые факты
[что не удалось проверить]

## 4. Использованные источники
[список URL]
"""
        
        # Получаем базовый промпт с навыком
        base_prompt = self._get_system_prompt(skill)
        system_prompt = base_prompt + "\n\n" + addendum
        
        tools_schema, tool_router = self._get_tools(config["tools"])
        
        logger.info(f"🔍 Создание чекера #{idx}: model={model}, max_iter={max_iter}")
        
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=system_prompt,
            tools_schema=tools_schema,
            tool_router=tool_router,
            usage_tracker=None,  # Будет передан из контекста
            role_name=f"checker-{idx}",
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
        )
    
    def create_arbiter(self, model: Optional[str] = None, skill: str = "fact-arbitration") -> BaseAgent:
        """
        Создаёт ArbiterAgent для сведения отчётов чекеров.
        
        Args:
            model: модель (по умолчанию arbiter из настроек)
            skill: имя навыка
        """
        config = self.DEFAULT_CONFIGS["arbiter"]
        model = model or self.settings.arbiter
        
        addendum = """
РЕЖИМ АРБИТРА v3.1 — КРАТКИЙ ФОРМАТ

Ты сводишь отчёты чекеров в итоговый вердикт.

Правила:
1. Фокус на фактических ошибках, не на стилистике.
2. Если источники нельзя ранжировать — ошибкой НЕ считаем.
3. Если чекер привёл данные первоисточника с URL — принимаем как корректное значение.
4. Отчёты вида «чекер недоступен» исключаются из сведения.

ФОРМАТ ОТЧЁТА:
# Итоговый отчёт фактчека (краткий)

## 1. Сводная таблица ошибок
| № | Цитата | Тип ошибки | Корректное значение | Согласие чекеров | Источники |

## 2. Ссылки на источники по чекерам
- Чекер 1 (model_name): [список URL]
- Чекер 2 (model_name): [список URL]

## 3. Выводы по чекерам (кратко)
- Чекер 1: [основной вывод]
- Чекер 2: [основной вывод]

## 4. Общий вывод
[Итоговое заключение]
"""
        
        base_prompt = self._get_system_prompt(skill)
        system_prompt = base_prompt + "\n\n" + addendum
        
        tools_schema, tool_router = self._get_tools(config["tools"])
        
        logger.info(f"⚖️ Создание арбитра: model={model}")
        
        return BaseAgent(
            client=self.client,
            model=model,
            system_prompt=system_prompt,
            tools_schema=tools_schema,
            tool_router=tool_router,
            usage_tracker=None,
            role_name="arbiter",
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
        )
