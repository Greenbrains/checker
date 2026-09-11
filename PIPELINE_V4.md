# Factcheck Agent Pipeline v4

## Обзор изменений v4

Версия 4 — рефакторинг пайплайна v3 с устранением ключевых проблем:

### Проблемы v3, которые исправлены в v4:

| Проблема v3 | Решение v4 |
|-------------|--------------|
| Оркестратор перегружен логикой | Тонкий оркестратор, логика в узлах графа |
| Чекер пересоздаётся на каждый чанк | Один чекер обрабатывает все свои факты |
| Бюджет итераций мал (3 на чанк) | Увеличен до 5-6 на набор фактов |
| Нет кеша источников между чекерами | Общий `SourceCache` для всех чекеров |
| Эмбеддер как LLM для нарезки | Программная нарезка (без LLM) |
| Арбитр получает все отчёты целиком | Суммаризация перед арбитражем |
| Нет дедупликации фактов | Дедупликация + распределение по чекерам |
| Пустые ответы съедают итерации | Исправлено в `BaseAgent.run()` |

## Архитектура v4

```
┌─────────────────────────────────────────────────────────────┐
│                    FactcheckOrchestratorV4                 │
│  (тонкий оркестратор: следует графу, не хранит состояние)   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      StateGraph                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │  Узел 1  │───►│    Узел 2    │───►│    Узел 3    │ ...  │
│  │  input   │    │  text_extract│    │ length_check │      │
│  │ analysis │    │              │    │              │      │
│  └──────────┘    └──────────────┘    └──────────────┘      │
│                                                             │
│  Граф состояний: 9 узлов с условными переходами            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PipelineContext                          │
│  - input_docs, extracted_text, chunks, facts               │
│  - checker_reports, source_cache, usage                    │
│  - Единое состояние, передаётся между узлами               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   SubagentFactory                           │
│  - create_reader()  (изображения/таблицы → текст)          │
│  - create_checker() (проверка фактов)                       │
│  - create_arbiter() (сведение отчётов)                      │
└─────────────────────────────────────────────────────────────┘
```

## Структура проекта

```
factcheck-agent/
├── agent/
│   ├── base.py                          # BaseAgent + UsageTracker
│   ├── orchestrator_v4.py              # Тонкий оркестратор v4
│   │
│   ├── state/                           # НОВОЕ: граф состояний
│   │   ├── __init__.py
│   │   ├── graph.py                     # StateGraph
│   │   ├── context.py                   # PipelineContext, Fact, Chunk
│   │   └── nodes/                       # Обработчики узлов
│   │       ├── input_analysis.py        # Анализ типа входа
│   │       ├── text_extraction.py       # Ридер изображений/таблиц
│   │       ├── length_check.py          # Проверка длины
│   │       ├── chunking.py              # Программная нарезка
│   │       ├── fact_extraction.py       # Извлечение + дедупликация
│   │       ├── fact_checking.py         # Запуск чекеров
│   │       ├── summarization.py         # Суммаризация отчётов
│   │       ├── arbitration.py           # Арбитраж
│   │       └── report.py                # Генерация отчёта
│   │
│   ├── subagents/                       # НОВОЕ: фабрика субагентов
│   │   ├── __init__.py
│   │   └── factory.py                   # SubagentFactory
│   │
│   ├── cache/                           # НОВОЕ: общий кеш
│   │   ├── __init__.py
│   │   └── source_cache.py              # SourceCache
│   │
│   └── core/                            # Без изменений
│       ├── tools/
│       ├── prompts/
│       └── readers/
│
├── .agents/skills/
│   ├── fact-checking/                   # Существующий навык
│   ├── image-reading/                   # НОВОЕ: OCR изображений
│   ├── table-parsing/                   # НОВОЕ: парсинг таблиц
│   └── fact-arbitration/                # НОВОЕ: краткий арбитраж
│
├── config/
├── interfaces/
├── input/
├── output/
├── main.py
└── requirements.txt
```

## Граф состояний

```
[НАЧАЛО]
    │
    ▼
[1. АНАЛИЗ ВХОДА] ── изображение/таблица ──► [2. ИЗВЛЕЧЕНИЕ ТЕКСТА]
    │                                            │
    └── текст ───────────────────────────────────┘
                                                 │
                                                 ▼
                                          [3. ПРОВЕРКА ДЛИНЫ]
                                                 │
                            ┌────────────────────┤
                            │                    │
                    длина > порога         длина ≤ порога
                            │                    │
                            ▼                    │
                   [4. НАРЕЗКА НА ЧАНКИ]         │
                   (программно, без LLM)         │
                            │                    │
                            ▼                    │
                   [5. ИЗВЛЕЧЕНИЕ ФАКТОВ] ◄──────┘
                   (дедупликация, приоритизация)
                            │
                            ▼
                   [6. ФАКТ-ЧЕКИНГ]
                   (чекеры по графу, кеш источников)
                            │
                            ▼
                   [7. СУММАРИЗАЦИЯ ОТЧЁТОВ]
                   (программно, без передачи полных текстов)
                            │
                            ▼
                   [8. АРБИТРАЖ]
                            │
                            ▼
                   [9. ГЕНЕРАЦИЯ ОТЧЁТА]
                            │
                            ▼
                        [КОНЕЦ]
```

## Запуск v4

```bash
# Через main.py (добавьте аргумент --version v4)
python main.py --version v4

# Или напрямую
python -c "from agent.orchestrator_v4 import run_v4; run_v4()"
```

## Конфигурация

Добавьте в `.env` новые параметры (опционально):

```bash
# Порог нарезки на чанки (символы)
CHUNKING_THRESHOLD=2000

# Бюджет итераций чекера (по умолчанию 6)
CHECKER_MAX_ITERATIONS=6

# TTL кеша источников (секунды, по умолчанию 3600)
SOURCE_CACHE_TTL=3600
```

## Улучшения токенов и итераций

### Экономия токенов:

| Компонент | v3 | v4 | Экономия |
|-----------|-----|------|----------|
| Нарезка чанков | ~1700 out | 0 | 100% |
| Системный промпт чекера | N чекеров × M чанков | N чекеров × 1 | ~60-75% |
| Контекст арбитра | ~14000 in | ~3000-5000 in | ~65-70% |

### Экономия итераций:

| Компонент | v3 | v4 | Эффект |
|-----------|-----|------|--------|
| Бюджет на чанк | 3 | 5-6 | +67-100% |
| Кеш источников | ❌ | ✅ | -20-40% запросов |
| Дедупликация фактов | ❌ | ✅ | -30-50% проверок |

## API модулей

### PipelineContext

```python
from agent.state import PipelineContext, Fact, Chunk

context = PipelineContext(
    settings=settings,
    source_cache={},  # Общий кеш
)

# Доступ к данным
context.facts  # Список извлечённых фактов
context.chunks  # Список чанков
context.checker_reports  # Отчёты чекеров
context.usage.total_tokens  # Метрики
```

### StateGraph

```python
from agent.state import StateGraph, PipelineContext

graph = StateGraph()
final_state = graph.execute(context)

if final_state.success:
    print(context.final_report)
else:
    print(f"Ошибка: {final_state.error}")
```

### SubagentFactory

```python
from agent.subagents import SubagentFactory

factory = SubagentFactory(client, settings, registry)

# Создание агентов
reader = factory.create_reader(model="qwen3.6-35b-a3b/latest")
checker = factory.create_checker(idx=1, model="z-ai/glm-5.3-flash")
arbiter = factory.create_arbiter(model="qwen3.6-35b-a3b/latest")
```

### SourceCache

```python
from agent.cache import SourceCache

cache = SourceCache(ttl_seconds=3600)

# Поиск с кешем
results = cache.get("Евровидение 2025 победитель")
if results is None:
    results = web_search("Евровидение 2025 победитель")
    cache.set("Евровидение 2025 победитель", results)
```

## Миграция с v3 на v4

1. **Оркестратор**: замените `FactcheckOrchestratorV3` на `FactcheckOrchestratorV4`
2. **Настройки**: добавьте `CHUNKING_THRESHOLD` (опционально)
3. **Скиллы**: убедитесь, что есть `image-reading`, `table-parsing`, `fact-arbitration`
4. **Запуск**: используйте `--version v4` или прямой импорт

## Тестирование

```bash
# Проверка импорта модулей
python -c "from agent.state import StateGraph, PipelineContext; print('OK')"
python -c "from agent.subagents import SubagentFactory; print('OK')"
python -c "from agent.cache import SourceCache; print('OK')"
python -c "from agent.orchestrator_v4 import FactcheckOrchestratorV4; print('OK')"
```

## Известные ограничения

- ReaderAgent для OCR изображений требует поддержки vision у модели
- Параллельный запуск чекеров не реализован (последовательно)
- MCP-скрапинг зарезервирован, но не подключён

## Планы развития

- v3.2: Параллельный запуск чекеров (async/concurrent)
- v3.3: MCP-скрапинг источников
- v3.4: Structured output чекеров (JSON mode)
- v3.5: Адаптивный бюджет итераций (динамическое распределение)
