"""
BaseAgent — движок агента с циклом tool-calling.
Version: 5.6.0
Description: Универсален для любого провайдера (URI модели берётся из settings).
Изменения 5.6.0:
    - пустой assistant-content не добавляется в историю (ряд провайдеров
      отклоняет сообщения с пустым content у assistant-роли);
    - MIN_REPORT_CHARS вынесена на уровень модуля;
    - импорт settings через get_settings() вместо прямого импорта переменной.
"""
import json
import logging
import time
from typing import Dict, List, Optional

from config.settings import get_settings

settings = get_settings()

logger = logging.getLogger("agent.base")

MIN_REPORT_CHARS = 300  # порог для определения «пустого» отчёта в оркестраторе


class UsageTracker:
    """Накопитель статистики использования токенов за сессию."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.request_count = 0
        self.total_time = 0.0

    def add(self, prompt: int, completion: int, duration: float):
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.request_count += 1
        self.total_time += duration

    def merge(self, other: 'UsageTracker'):
        """Сливает статистику из другого трекера."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.request_count += other.request_count
        self.total_time += other.total_time

    def summary(self) -> str:
        return (
            f"📊 Сессия: {self.request_count} запросов | "
            f"⏱️ {self.total_time:.2f}s | "
            f"🔤 {self.prompt_tokens} in / {self.completion_tokens} out / {self.total_tokens} total"
        )


class BaseAgent:
    """Агент с циклом tool-calling для активного провайдера."""

    def __init__(
        self,
        client,
        model: str,
        system_prompt: str,
        tools_schema: Optional[list] = None,
        tool_router: Optional[dict] = None,
        usage_tracker: Optional[UsageTracker] = None,
        role_name: str = "agent",
        temperature: float = 0.3,
        max_tokens: int = 16384,
    ):
        self.client = client
        self.model_name = model
        self.model_uri = settings.build_model_uri(model)
        self.system_prompt = system_prompt
        self.tools_schema = tools_schema or []
        self.tool_router = tool_router or {}
        self.usage = usage_tracker or UsageTracker()
        self.role_name = role_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def run(self, user_message: str, history: List[Dict] = None, max_iterations: int = 10) -> str:
        history = history or []
        if not history:
            history.append({"role": "system", "content": self.system_prompt})
        history.append({"role": "user", "content": user_message})

        session_start = time.time()
        logger.info(f"🤖 [{self.role_name}] model={self.model_name} (uri={self.model_uri})")

        for i in range(max_iterations):
            logger.info(f"  [{self.role_name}] iteration {i + 1}/{max_iterations}")
            start_t = time.time()

            try:
                response = self.client.chat.completions.create(
                    model=self.model_uri,
                    messages=history,
                    tools=self.tools_schema if self.tools_schema else None,
                    tool_choice="auto" if self.tools_schema else None,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as e:
                logger.error(f"❌ [{self.role_name}] API error: {e}")
                raise

            duration = time.time() - start_t
            msg = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            usage = getattr(response, "usage", None)
            prompt_t = getattr(usage, "prompt_tokens", 0) if usage else 0
            compl_t = getattr(usage, "completion_tokens", 0) if usage else 0
            self.usage.add(prompt_t, compl_t, duration)
            logger.debug(
                f"  [{self.role_name}] tokens={prompt_t}+{compl_t} | "
                f"time={duration:.2f}s | finish={finish_reason}"
            )

            if msg.content and msg.tool_calls:
                logger.info(f"    💭 {' '.join(msg.content.split())[:140]}...")

            # Финальный ответ
            if finish_reason == "stop" and not msg.tool_calls:
                content = msg.content or ""
                if not content.strip():
                    # Пустой assistant-message не добавляем — ряд провайдеров его отклоняет
                    logger.warning(f"⚠️ [{self.role_name}] пустой ответ — пинаю...")
                    history.append({
                        "role": "user",
                        "content": "[Система: предыдущий ответ был пустым. Продолжай выполнение задачи.]",
                    })
                    continue
                logger.info(
                    f"✅ [{self.role_name}] done in {time.time() - session_start:.2f}s "
                    f"(tokens {prompt_t}+{compl_t})"
                )
                return content

            # Ответ обрезан по длине
            if finish_reason == "length" and not msg.tool_calls:
                logger.warning(f"⚠️ [{self.role_name}] ответ обрезан — продолжаю...")
                history.append({"role": "assistant", "content": msg.content or ""})
                history.append({
                    "role": "user",
                    "content": (
                        "[Система: предыдущий ответ был обрезан по длине. "
                        "Продолжи ровно с места остановки, не повторяя уже написанное.]"
                    ),
                })
                continue

            # Tool calls
            if msg.tool_calls:
                history.append(msg.model_dump())
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    short_args_parts = []
                    for k, v in args.items():
                        sv = str(v)
                        if k == "code":
                            short_args_parts.append(f"{k}='<{len(sv)} симв.>'")
                        elif len(sv) > 80:
                            short_args_parts.append(f"{k}={sv[:77]}...")
                        else:
                            short_args_parts.append(f"{k}={sv!r}")
                    short_args = ", ".join(short_args_parts)[:150]
                    logger.info(f"    🔧 {tc.function.name}({short_args})")
                    logger.debug(f"    FULL ARGS: {json.dumps(args, ensure_ascii=False)[:2000]}")

                    func = self.tool_router.get(tc.function.name)
                    if func:
                        try:
                            result = func(**args)
                            status = "✓"
                        except Exception as e:
                            result = f"❌ Ошибка: {str(e)}"
                            status = "✗"
                            logger.exception(f"Ошибка в {tc.function.name}")
                    else:
                        result = f"❌ Инструмент не найден: {tc.function.name}"
                        status = "✗"

                    result_str = str(result)
                    logger.info(f"       {status} {tc.function.name} | {len(result_str)} симв.")
                    logger.debug(f"       FULL RESULT ({len(result_str)} симв.):\n{result_str[:3000]}")

                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
                continue

            break

        # Бюджет исчерпан — финальный отчёт без инструментов
        logger.warning(
            f"⚠️ [{self.role_name}] бюджет итераций исчерпан — финальный отчёт без тулов"
        )
        history.append({
            "role": "user",
            "content": (
                "[Система: бюджет вызовов инструментов исчерпан. Составь итоговый отчёт "
                "ПРЯМО СЕЙЧАС по уже собранным данным, в формате навыка, без вызовов инструментов.]"
            ),
        })
        try:
            final_resp = self.client.chat.completions.create(
                model=self.model_uri,
                messages=history,
                tools=None,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            f_usage = getattr(final_resp, "usage", None)
            if f_usage:
                self.usage.add(
                    getattr(f_usage, "prompt_tokens", 0),
                    getattr(f_usage, "completion_tokens", 0),
                    0.0,
                )
            content = final_resp.choices[0].message.content or ""
            if content.strip():
                logger.info(f"✅ [{self.role_name}] финальный отчёт после исчерпания бюджета")
                return content
        except Exception as e:
            logger.error(f"❌ [{self.role_name}] финальный вызов не удался: {e}")

        return f"❌ Превышено количество итераций ({max_iterations})."
