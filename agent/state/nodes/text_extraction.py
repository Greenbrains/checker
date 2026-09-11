"""
Узел 2: Извлечение текста из изображений/таблиц.
Картинки распознаёт VLM-модель активного провайдера (одним вызовом, без агентского цикла).
"""
import base64
import logging
import mimetypes
from pathlib import Path
from typing import Optional

from agent.state.context import PipelineContext, PipelineState
from agent.state.text_utils import normalize_text

logger = logging.getLogger("agent.state.nodes.text_extraction")

VLM_PROMPT = (
    "Извлеки весь видимый текст дословно, сохраняя строки и таблицы в Markdown. "
    "Ничего не добавляй от себя, не комментируй. Если текста нет — ответь: (текста нет)."
)


def _image_data_url(doc: dict) -> Optional[str]:
    """Достаёт картинку в виде data:URL из doc или с диска."""
    data_url = doc.get("data_url")
    if isinstance(data_url, str) and data_url.startswith("data:image"):
        return data_url
    raw = doc.get("base64") or doc.get("b64")
    if isinstance(raw, str) and raw:
        mime = doc.get("mime") or "image/png"
        return f"data:{mime};base64,{raw}"
    path = doc.get("path") or doc.get("file")
    if path:
        p = Path(path)
        if p.is_file():
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    return None


def _vlm_extract(context: PipelineContext, data_url: str, name: str) -> str:
    """Один вызов VLM на изображение. Токены пишутся в общий трекер."""
    client = context.metadata.get("client")
    if client is None:
        from openai import OpenAI
        p = context.settings.provider
        client = OpenAI(api_key=p.api_key, base_url=p.base_url,
                        timeout=context.settings.request_timeout)
        context.metadata["client"] = client
    model_uri = context.settings.build_model_uri(context.settings.model_agent)
    resp = client.chat.completions.create(
        model=model_uri,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": VLM_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        temperature=0.0,
        max_tokens=4096,
    )
    u = getattr(resp, "usage", None)
    if u:
        context.usage.add(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), 0.0)
    logger.info("  🖼️ %s распознано через %s", name, model_uri)
    return (resp.choices[0].message.content or "").strip()


def extract_text(context: PipelineContext) -> PipelineState:
    """Дополняет extracted_text текстом из картинок и таблиц."""
    logger.info("📖 Извлечение текста из изображений/таблиц")

    if context.settings is None:
        return PipelineState(success=False, error="context.settings не задан", context=context)

    docs = [d for d in context.input_docs if str(d.get("kind") or "").lower() in ("image", "table")]
    if not docs:
        logger.info("✅ Изображений/таблиц нет — пропускаю")
        return PipelineState(context=context)

    parts = [context.extracted_text] if context.extracted_text else []

    for doc in docs:
        kind = str(doc.get("kind") or "").lower()
        name = doc.get("name", "unknown")

        if kind == "table":
            body = normalize_text(doc.get("content") or doc.get("text") or "")
            parts.append(f"--- Таблица: {name} ---\n{body}")
            logger.info("  📊 Таблица %s: %d симв.", name, len(body))
            continue

        data_url = _image_data_url(doc)
        if not data_url:
            parts.append(f"[Изображение: {name} — нет данных для распознавания]")
            logger.warning("⚠️ Изображение %s: нет base64/пути — OCR пропущен", name)
            continue
        try:
            parts.append(f"--- Изображение: {name} ---\n{_vlm_extract(context, data_url, name)}")
        except Exception as e:
            logger.error("❌ OCR %s не удался: %s", name, e)
            parts.append(f"[Изображение: {name} — распознавание не удалось: {e}]")

    context.extracted_text = normalize_text("\n\n".join(p for p in parts if p))
    context.text_length = len(context.extracted_text)
    logger.info("✅ Текст после ридера: %d симв.", context.text_length)
    return PipelineState(context=context)