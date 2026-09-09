"""
📋 agent_tools.py — фабрика инструментов factcheck-агента.
Version: 1.2.0
Description:
    - декоратор @tool: автосхема OpenAI function-calling из сигнатуры и Annotated;
    - навыки: каталог + load_skill (.agents/skills/<name>/SKILL.md + references/);
    - локальные инструменты: bash_execute, file_read, file_write;
    - веб-инструменты: web_search (DuckDuckGo), web_read (читалка страниц),
      code_execute (локальная песочница).
Изменения 1.2.0: YandexTools и folder_id удалены полностью; legacy fallback удалён;
импорт DuckDuckGo один (duckduckgo_search), без ddgs.
"""
import inspect
import logging
import os
import re
import subprocess
import sys
import tempfile
from functools import wraps
from pathlib import Path
from typing import Annotated, get_args, get_origin, get_type_hints
from urllib.parse import urlparse, quote_plus, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

from config.settings import settings

logger = logging.getLogger("agent.tools")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_CODE_OUTPUT_CHARS = 8000


# ============================================================
# Фабрика @tool и схемы
# ============================================================

def _python_type_to_json(python_type) -> str:
    origin = get_origin(python_type)
    if origin is not None:
        args = get_args(python_type)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json(non_none[0])
    type_map = {
        str: "string", int: "integer", float: "number",
        bool: "boolean", list: "array", dict: "object",
    }
    return type_map.get(python_type, "string")


def _extract_parameters_schema(fn) -> dict:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:
        hints = {}
    properties, required = {}, []
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "client"):
            continue
        param_type = hints.get(param_name, str)
        param_description = ""
        actual_type = param_type
        if hasattr(param_type, "__metadata__"):
            actual_type = param_type.__args__[0]
            if param_type.__metadata__:
                param_description = param_type.__metadata__[0]
        prop_schema = {"type": _python_type_to_json(actual_type)}
        if param_description:
            prop_schema["description"] = param_description
        properties[param_name] = prop_schema
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool(func=None, *, name: str = None, description: str = None):
    def decorator(fn):
        tool_name = name or fn.__name__
        tool_description = description or (fn.__doc__ or " ").strip()
        tool_schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": _extract_parameters_schema(fn),
            },
        }

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        for obj in (fn, wrapper):
            obj._tool_schema = tool_schema
            obj._tool_name = tool_name
            obj._tool_description = tool_description
        return wrapper

    return decorator(func) if func is not None else decorator


def collect_tools(*tool_functions) -> list:
    return [fn._tool_schema for fn in tool_functions if hasattr(fn, "_tool_schema")]


def create_tool_router(*tool_functions) -> dict:
    return {fn._tool_name: fn for fn in tool_functions if hasattr(fn, "_tool_name")}


# ============================================================
# Навыки: .agents/skills/<name>/SKILL.md + references/
# ============================================================

def load_skills_catalog() -> str:
    catalog_file = settings.skills_dir / "SKILL.md"
    if catalog_file.exists():
        return catalog_file.read_text(encoding="utf-8")
    if settings.skills_dir.exists():
        names = sorted(p.name for p in settings.skills_dir.iterdir() if p.is_dir())
        if names:
            return "Обнаружены навыки (без описаний):\n" + "\n".join(f"- {n}" for n in names)
    return "Каталог навыков пуст."


@tool
def load_skill(
    skill_name: Annotated[str, "Имя навыка из каталога, напр. 'fact-checking'. Пусто — вернуть каталог всех навыков."] = "",
    include_references: Annotated[bool, "Подгрузить ли справочники references/*.md"] = True,
) -> str:
    """Загружает инструкцию навыка: .agents/skills/<name>/SKILL.md (+ справочники references/*.md)."""
    if not skill_name:
        return load_skills_catalog()
    skill_path = settings.skills_dir / skill_name / "SKILL.md"
    if not skill_path.exists():
        return f"❌ Навык '{skill_name}' не найден. Вызови load_skill() без аргумента для списка."
    text = skill_path.read_text(encoding="utf-8")
    if text.startswith("---"):                       # срезаем YAML-frontmatter
        chunks = text.split("---", 2)
        if len(chunks) >= 3:
            text = chunks[2]
    if include_references:
        ref_dir = skill_path.parent / "references"
        if ref_dir.exists():
            for f in sorted(ref_dir.glob("*.md")):
                text += f"\n\n---\n# СПРАВОЧНИК: {f.name}\n\n" + f.read_text(encoding="utf-8")
    return text.strip()


# ============================================================
# Локальные инструменты
# ============================================================

@tool
def bash_execute(command: Annotated[str, "Bash-команда для локального выполнения."]) -> str:
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return result.stdout or "✅ Выполнено успешно (без вывода)"
        return f"❌ Ошибка (код {result.returncode}):\n{result.stderr}"
    except subprocess.TimeoutExpired:
        return "❌ Превышено время выполнения (60 секунд)"
    except Exception as e:
        return f"❌ Исключение: {str(e)}"


@tool
def file_read(file_path: Annotated[str, "Путь к локальному файлу для чтения."]) -> str:
    path = Path(file_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"❌ Файл не найден: {file_path}"


@tool
def file_write(
    file_path: Annotated[str, "Путь к локальному файлу для записи."],
    content: Annotated[str, "Содержимое для записи в файл."],
) -> str:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"✅ Файл сохранён: {file_path}"


# ============================================================
# Веб-инструменты
# ============================================================

def _format_search_results(results: list) -> str:
    parts = ["🔍 Результаты поиска:\n"]
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r.get('title') or '(без заголовка)'}")
        parts.append(f"   URL: {r.get('href') or r.get('url') or 'N/A'}")
        body = (r.get('body') or '').strip()
        if body:
            parts.append(f"   {body}")
        parts.append("")
    return "\n".join(parts).strip()


def _ddg_html_search(query: str, max_results: int) -> str:
    """Фолбэк: прямой парсинг html.duckduckgo.com, когда bing-бэкенд недоступен."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
    except Exception as e:
        return f"❌ Поиск недоступен: {e}. Иди на сайт первоисточника напрямую через web_read."
    soup = BeautifulSoup(resp.text, "html.parser")
    parts = ["🔍 Результаты поиска (html.duckduckgo.com):\n"]
    n = 0
    for a in soup.select("a.result__a"):
        if n >= min(max_results, 10):
            break
        href = a.get("href", "")
        if "uddg=" in href:                      # распаковка редиректа DDG
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snippet_el = a.find_parent("div", class_="result")
        snippet = ""
        if snippet_el:
            s = snippet_el.select_one(".result__snippet")
            snippet = s.get_text(strip=True) if s else ""
        n += 1
        parts.append(f"{n}. {a.get_text(strip=True)}")
        parts.append(f"   URL: {href}")
        if snippet:
            parts.append(f"   {snippet}")
        parts.append("")
    if n == 0:
        return "❌ Поиск не дал результатов. Переформулируй или добавь site:/кавычки."
    return "\n".join(parts).strip()


@tool
def web_search(
    query: Annotated[str, "Поисковый запрос. Операторы: site:, \"точная фраза\", -, filetype:"],
    max_results: Annotated[int, "Максимум результатов (1-10)"] = 5,
) -> str:
    """Поиск в интернете (DuckDuckGo, регион ru-ru). При сбое бэкенда — фолбэк на html.duckduckgo.com."""
    from duckduckgo_search import DDGS

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="ru-ru",
                                     max_results=min(max(max_results, 1), 10)))
        if results:
            return _format_search_results(results)
    except Exception as e:
        logger.warning(f"DDGS-бэкенд сбоит ({e}); фолбэк на html-эндпоинт")
    return _ddg_html_search(query, max_results)


@tool
def web_read(
    url: Annotated[str, "Полный URL страницы (http/https)"],
    max_chars: Annotated[int, "Максимум символов текста к возврату"] = 6000,
) -> str:
    """Загружает веб-страницу и извлекает основной текст (без меню/рекламы/скриптов). Для чтения первоисточников."""
    max_chars = min(max(int(max_chars), 500), 8000)   # защита контекста от простыней
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "❌ Поддерживаются только http/https URL."
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url.strip(), headers={"User-Agent": _UA})
            resp.raise_for_status()
    except httpx.TimeoutException:
        return f"❌ Таймаут загрузки страницы (15 сек): {url}"
    except httpx.HTTPStatusError as e:
        return f"❌ HTTP {e.response.status_code} при загрузке {url}"
    except Exception as e:
        return f"❌ Ошибка загрузки страницы: {e}"
    enc = resp.encoding or "utf-8"
    try:
        html = resp.content.decode(enc, errors="replace")
    except (LookupError, UnicodeError):
        html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "iframe", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    main = (soup.find("main") or soup.find("article")
            or soup.find(attrs={"role": "main"}) or soup.body or soup)
    text = main.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    total = len(text)
    if total > max_chars:
        text = text[:max_chars] + f"\n\n[… обрезано: всего на странице {total} симв.]"
    head = f"📄 {url}"
    if title:
        head += f"\nЗаголовок: {title}"
    if not text:
        return f"{head}\n\n❌ Не удалось извлечь текст (возможно, страница рендерится JavaScript)."
    return f"{head}\n\n{text}"


# ============================================================
# Песочница кода
# ============================================================

_DANGEROUS_PATTERNS = [
    r"\bos\s*\.\s*system\s*\(",
    r"\bsubprocess\s*\.",
    r"__import__\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bopen\s*\([^)]*[\"'][wax]",
    r"\bshutil\s*\.\s*rmtree",
]


@tool
def code_execute(
    code: Annotated[str, "Python-код для выполнения (расчёты, обработка данных; без записи файлов и сети)"],
    timeout: Annotated[int, "Таймаут в секундах (1-60)"] = 30,
) -> str:
    """Выполняет Python-код в локальной изолированной песочнице: проверка расчётов, дат, таблиц."""
    for pat in _DANGEROUS_PATTERNS:
        if re.search(pat, code):
            return "❌ Отказ: в коде запрещённые операции (subprocess/exec/eval/запись файлов/rmtree)."
    timeout = min(max(int(timeout), 1), 60)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        env = {k: os.environ[k] for k in
               ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE") if k in os.environ}
        result = subprocess.run(
            [sys.executable, "-I", tmp_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=tempfile.gettempdir(), env=env,
        )
        parts = []
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if out:
            parts.append("**Вывод:**\n```\n" + out[:MAX_CODE_OUTPUT_CHARS] + "\n```")
        if err:
            parts.append("**Ошибки:**\n```\n" + err[:MAX_CODE_OUTPUT_CHARS] + "\n```")
        if result.returncode != 0:
            parts.append(f"**Код завершения:** {result.returncode}")
        return "\n\n".join(parts) if parts else "✅ Код выполнен успешно (без вывода)."
    except subprocess.TimeoutExpired:
        return f"❌ Превышено время выполнения ({timeout} сек)."
    except Exception as e:
        return f"❌ Ошибка выполнения: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ============================================================
# Реестр «навык → инструменты»
# ============================================================

SKILL_TOOLSETS = {
    "general": [
        "bash_execute", "file_read", "file_write",
    ],
    # === Fact-checking: только верификация; материалы уже в сообщении ===
    "fact-checking": [
        "web_search", "web_read", "code_execute",
    ],
}


def filter_tools_for_skill(all_tool_funcs, skill_name: str):
    allowed = SKILL_TOOLSETS.get(skill_name)
    if not allowed:
        return list(all_tool_funcs)
    allowed_set = set(allowed)
    return [fn for fn in all_tool_funcs if getattr(fn, "_tool_name", None) in allowed_set]