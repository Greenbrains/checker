"""
agent/core/tools/utils.py — чистые утилиты пайплайнов (без LLM и без сети).
Version: 1.0.0
Description:
- parse_json: извлечение первого сбалансированного {...} из ответа модели;
- dispatch: группировка claims в кластеры {key, skill, claims} по типу/источнику.
История: 1.0.0 — вынесено из agent/orchestrator_v2.py (оркестратор только оркестрирует).
"""
import json
import re
from typing import Dict, List, Optional

# типы claims, проверяемые через веб
WEB_TYPES = {"org_name", "person", "date_age", "geo", "number_external"}


def parse_json(text: str) -> Optional[dict]:
    """Достаёт первый сбалансированный {...} из ответа модели."""
    if not text:
        return None
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    start = clean.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(clean)):
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(clean[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def dispatch(claims: List[dict]) -> List[dict]:
    """Чистый dispatcher: claims → кластеры {key, skill, claims}."""
    clusters: Dict[str, dict] = {}
    for c in claims:
        ctype = c.get("type", "number_external")
        if ctype == "math_logic":
            key, skill = "math", "verify-math"
        elif ctype == "grammar":
            key, skill = "grammar", "verify-grammar"
        elif ctype == "consistency":
            continue                      # consistency разбирает арбитр
        elif ctype in WEB_TYPES:
            hint = (c.get("source_hint") or "web").strip().lower() or "web"
            if "wikipedia" in hint:       # Wikipedia заблокирована в РФ — ищем в общем вебе
                hint = "web"
            hint = re.sub(r"^https?://", "", hint)
            hint = re.sub(r"^www\.", "", hint).split("/")[0]
            key, skill = f"web:{hint}", "verify-web"
        else:
            key, skill = "web:other", "verify-web"
        clusters.setdefault(key, {"key": key, "skill": skill, "claims": []})["claims"].append(c)
    return list(clusters.values())