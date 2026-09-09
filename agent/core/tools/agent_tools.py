"""
agent_tools.py — фабрика инструментов factcheck-агента.
Version: 2.0.0
Description:
    - декоратор @tool: автосхема OpenAI function-calling из сигнатуры и Annotated;
    - навыки: load_skill (.agents/skills/<name>/SKILL.md + references/);
    - веб: web_search (метасерч ddgs: duckduckgo → brave → google → yahoo),
      web_read (читалка страниц с ретраем);
    - песочница: code_execute (локальная изолированная).

Изменения 2.0.0:
    - удалены неиспользуемые инструменты: bash_execute, file_read, file_write;
    - удалена filter_tools_for_skill / SKILL_TOOLSETS — логика живёт в registry и оркестраторе;
    - убран _ddg_html_search (фолбэк-парсер DDG html) — нестабилен, дублирует ddgs;
    - _rank_results / _domain_score / _tokens / JUNK_DOMAINS вынесены в _search_utils;
    - load_skills_catalog стала приватной (_load_skills_catalog).
    - импорт settings через get_settings() вместо прямого импорта переменной.
"""
import inspect
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from functools import wraps
from pathlib import Path
from typing import Annotated, get_args, get_origin, get_type_hints
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

from config.settings import get_settings

settings = get_settings()

from ddgs import DDGS

# Живые бэкенды ddgs (проверено на установленном пакете): yandex отсутствует!
_SEARCH_BACKENDS = ("duckduckgo", "brave", "google", "yahoo")
logging.getLogger("ddgs").setLevel(logging.ERROR)  # не спамим консоль кухней движков

logger = logging.getLogger("agent.tools")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
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
        description = ""
        actual_type = param_type
        if hasattr(param_type, "__metadata__"):
            actual_type = param_type.__args__[0]
            if param_type.__metadata__:
                description = param_type.__metadata__[0]
        prop = {"type": _python_type_to_json(actual_type)}
        if description:
            prop["description"] = description
        properties[param_name] = prop
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

def _load_skills_catalog() -> str:
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
        return _load_skills_catalog()
    skill_path = settings.skills_dir / skill_name / "SKILL.md"
    if not skill_path.exists():
        return f"❌ Навык '{skill_name}' не найден. Вызови load_skill() без аргумента для списка."
    text = skill_path.read_text(encoding="utf-8")
    if text.startswith("# REDIRECT:"):
        target = text.split(":", 1)[1].strip()
        skill_path = settings.skills_dir / target / "SKILL.md"
        if skill_path.exists():
            text = skill_path.read_text(encoding="utf-8")
    if not include_references:
        return text
    refs_dir = skill_path.parent / "references"
    if not refs_dir.is_dir():
        return text
    parts = [text]
    for ref in sorted(refs_dir.glob("*.md")):
        parts.append(f"\n\n---\n## Справочник: {ref.stem}\n\n{ref.read_text(encoding='utf-8')}")
    return "".join(parts)


# ============================================================
# Веб: утилиты фильтрации и ранжирования
# ============================================================

# Wikipedia заблокирована в РФ — 403 на всех эндпоинтах
_BLOCKED_DOMAINS = {"wikipedia.org"}

# Домены без первоисточников: форумы, астрология, соцсети, мусор
_JUNK_DOMAINS = {
    "otvet.mail.ru", "answers.yahoo.com", "reddit.com", "quora.com",
    "pikabu.ru", "irecommend.ru", "otzovik.com", "yandex.ru/q",
    "dzen.ru", "zen.yandex.ru",
    "forums.vr-zone.com", "hotukdeals.com", "bolshoyvopros.ru",
    "zhihu.com", "baike.baidu.com",
    "astromeridian.ru", "horo.mail.ru", "znachenieimeny.ru", "namedb.ru",
    "instagram.com", "youtube.com", "vk.com", "yandex.ru/video",
    "spletnik.ru", "24smi.org", "fandom.com", "facebook.com",
    "moneysavingexpert.com", "ispreview.co.uk", "cellphones.com.vn",
    "registroimprese.it", "mnt.fr",
}


def _domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def _is_blocked(url: str) -> bool:
    return any(b in _domain_of(url) for b in _BLOCKED_DOMAINS)


def _is_junk(url: str) -> bool:
    return any(j in _domain_of(url) for j in _JUNK_DOMAINS)


def _domain_score(url: str) -> int:
    d = _domain_of(url)
    if d.endswith((".ru", ".рф", ".su", ".by", ".kz")):
        return 3
    if d.endswith(".org"):
        return 0
    return 1


def _tokens(text: str) -> set:
    text = text.lower().replace(",", ".")
    return {t for t in re.findall(r"[a-zа-яё0-9][a-zа-яё0-9.\-]*", text)
            if len(t) >= 3 or t.isdigit()}


def _rank_results(query: str, results: list) -> list:
    """Режет нерелевантный мусор и сортирует: релевантность + приоритет домена."""
    q = _tokens(query)
    if not q:
        return []
    min_overlap = 2 if len(q) >= 6 else 1
    scored = []
    for r in results:
        href = r.get("href") or r.get("url") or ""
        if not href or _is_junk(href) or _is_blocked(href):
            continue
        overlap = len(q & _tokens(f"{r.get('title', '')} {r.get('body', '')}"))
        if overlap < min_overlap:
            continue
        scored.append((overlap * 2 + _domain_score(href), r))
    scored.sort(key=lambda p: p[0], reverse=True)
    return [r for _, r in scored]


def _format_search_results(results: list, backend: str = "") -> str:
    head = f"🔍 Результаты поиска (backend: {backend}):" if backend else "🔍 Результаты поиска:"
    parts = [head, ""]
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r.get('title') or '(без заголовка)'}")
        parts.append(f"   URL: {r.get('href') or r.get('url') or 'N/A'}")
        body = (r.get("body") or "").strip()
        if body:
            parts.append(f"   {body}")
        parts.append("")
    return "\n".join(parts).strip()


# ============================================================
# Инструмент: web_search
# ============================================================

@tool
def web_search(
    query: Annotated[str, "Поисковый запрос. Операторы: site:, \"точная фраза\", -, filetype:. Для RU-контента формулируй по-русски."],
    max_results: Annotated[int, "Максимум результатов (1-10)"] = 5,
) -> str:
    """Поиск в интернете (метасерч ddgs: duckduckgo → brave → google → yahoo, регион ru-ru).
    Wikipedia заблокирована в РФ — не ищи там. Мусор отфильтровывается по релевантности."""
    if "wikipedia" in query.lower():
        query = re.sub(r"site:\S*wikipedia\S*", "", query, flags=re.IGNORECASE).strip()
        query = re.sub(r"wikipedia\S*", "", query, flags=re.IGNORECASE).strip()
        if not query:
            return "❌ Wikipedia заблокирована в РФ. Используй альтернативные источники."

    limit = min(max(max_results, 1), 10)
    raw, backend = [], ""

    for b in _SEARCH_BACKENDS:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, region="ru-ru", max_results=limit, backend=b))
            if raw:
                backend = b
                break
        except Exception as e:
            logger.debug(f"ddgs backend={b} ошибка: {e}")
            continue

    if not raw:
        return f"❌ Поиск не дал результатов по запросу: {query!r}"

    ranked = _rank_results(query, raw)
    if not ranked:
        ranked = raw[:limit]

    return _format_search_results(ranked[:limit], backend)


# ============================================================
# Инструмент: web_read
# ============================================================

@tool
def web_read(
    url: Annotated[str, "URL страницы для чтения"],
    max_chars: Annotated[int, "Максимум символов в результате (500-16000)"] = 8000,
) -> str:
    """Читает веб-страницу и возвращает очищенный текст. Ретрай при обрыве связи."""
    if _is_blocked(url):
        return f"❌ {_domain_of(url)} заблокирован в РФ (403). Используй другой источник."

    max_chars = min(max(max_chars, 500), 16000)
    last_error = ""

    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": _UA},
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
            break
        except httpx.HTTPStatusError as e:
            return f"❌ HTTP {e.response.status_code}: {url}"
        except Exception as e:
            last_error = str(e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    else:
        return f"❌ Не удалось загрузить страницу после 3 попыток: {last_error}"

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(id=re.compile(r"content|main|article", re.I))
        or soup.find(class_=re.compile(r"content|main|article|post|text", re.I))
        or soup.body
    )
    raw_text = (main or soup).get_text(separator="\n")
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
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
# Инструмент: code_execute
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
               ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")
               if k in os.environ}

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
