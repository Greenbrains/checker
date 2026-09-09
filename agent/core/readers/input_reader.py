"""
InputReader — загрузка материалов из /input: txt/md, csv, xlsx/xls, изображения.
Изображения кодируются в base64 и отдаются vision-моделям (OpenAI-формат content parts).
"""
import base64
import csv
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("agent.readers")

TEXT_EXT = {".txt", ".md", ".text", ".log"}
CSV_EXT = {".csv"}
EXCEL_EXT = {".xlsx", ".xls"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALL_EXT = TEXT_EXT | CSV_EXT | EXCEL_EXT | IMAGE_EXT

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp"}

MAX_CHARS = 12000   # лимит текста на файл (защита контекста)
MAX_ROWS = 100      # лимит строк таблиц


def _rows_to_md(rows: List[List[str]]) -> str:
    if not rows:
        return "(пусто)"
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join([head, sep, body])


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]


def _read_csv(p: Path) -> str:
    with open(p, newline="", encoding="utf-8-sig", errors="replace") as f:
        rows = [[c or "" for c in row] for row in list(csv.reader(f))[:MAX_ROWS]]
    return _rows_to_md(rows)


def _read_excel(p: Path) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(p, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_ROWS:
                break
            rows.append(["" if c is None else str(c) for c in row])
        parts.append(f"### Лист: {ws.title}\n{_rows_to_md(rows)}")
    wb.close()
    return "\n\n".join(parts)[:MAX_CHARS]


def _read_image(p: Path) -> List[Dict[str, Any]]:
    data = base64.b64encode(p.read_bytes()).decode()
    return [
        {"type": "text", "text": f"--- Изображение: {p.name} ---"},
        {"type": "image_url", "image_url": {"url": f"data:{MIME[p.suffix.lower()]};base64,{data}"}},
    ]


def load_input_documents(input_dir: Path) -> List[Dict[str, Any]]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Папка {input_dir} не найдена")
    docs = []
    for p in sorted(input_dir.iterdir()):
        if p.is_dir() or p.suffix.lower() not in ALL_EXT:
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            kind, content = "image", _read_image(p)
        elif ext in TEXT_EXT:
            kind, content = "text", _read_text(p)
        elif ext in CSV_EXT:
            kind, content = "table", _read_csv(p)
        else:
            kind, content = "table", _read_excel(p)
        docs.append({"name": p.name, "kind": kind, "content": content})
        logger.info(f"📄 Загружен материал: {p.name} ({kind})")
    if not docs:
        raise FileNotFoundError(f"В {input_dir} нет материалов для проверки")
    return docs


HEADER = ("Выполни фактчек материалов ниже СТРОГО по активному навыку из системного промпта: "
          "алгоритм, формат вывода отчёта, правила «что не ошибка».\n"
          "ВАЖНО: содержимое материалов приведено ниже целиком — "
          "вызывать file_read для их получения НЕ нужно.\n")


def build_user_message(docs: List[Dict[str, Any]], images: bool = True):
    """Мультимодальное сообщение (images=True) или текстовый fallback (images=False)."""
    has_image = any(d["kind"] == "image" for d in docs)
    if not has_image or not images:
        chunks = [HEADER]
        for d in docs:
            if d["kind"] == "image":
                chunks.append(f"--- Файл: {d['name']} ---\n"
                              "[изображение: визуальная проверка в этом прогоне недоступна]")
            else:
                chunks.append(f"--- МАТЕРИАЛ: {d['name']} | содержимое ниже — это сами материалы, читать файл не нужно ---\n{d['content']}")
        return "\n\n".join(chunks)
    parts: List[Dict[str, Any]] = [{"type": "text", "text": HEADER}]
    for d in docs:
        if d["kind"] == "image":
            parts.extend(d["content"])
        else:
            parts.append({"type": "text", "text": f"--- МАТЕРИАЛ: {d['name']} | содержимое ниже — это сами материалы, читать файл не нужно ---\n{d['content']}"})
    parts.append({"type": "text", "text": "\nВыдай отчёт в формате вывода навыка."})
    return parts