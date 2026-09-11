"""
Конфигурация factcheck-agent.
Version: 2.2.0
Изменения 2.2.0:
    - приватные поля объявлены через PrivateAttr (pydantic v2 без сюрпризов);
    - добавлены параметры инференса: параллелизм чекеров, бюджеты итераций,
      TTL кеша источников, пороги чанкинга, ретраи API, ротация логов;
    - валидация списка чекеров осталась в model_validator(mode="after"),
      поэтому @lru_cache на get_settings() не ломается.
"""
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    uri_format: str = "{model}"
    display_name: str = ""


class Settings(BaseSettings):
    # ---------- секреты ----------
    yandex_api_key: Optional[str] = None
    yandex_folder_id: Optional[str] = None
    router_ai_key: Optional[str] = None
    default_provider: str = "router"

    # ---------- базовые модели провайдеров ----------
    yandex_model_router: str = "aliceai-llm-flash/latest"
    yandex_model_agent: str = "qwen3.6-35b-a3b/latest"
    router_model: str = "z-ai/glm-5.3-flash"

    # ---------- роли ----------
    checker_models: str = "z-ai/glm-5.3-flash,google/gemini-3.5-flash-lite"
    arbiter_model: str = ""     # пусто => model_agent активного провайдера
    extractor_model: str = ""   # пусто => model_router
    reader_model: str = ""      # пусто => model_agent

    # ---------- каталоги и границы ----------
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    num_checkers_min: int = 2
    num_checkers_max: int = 3

    # ---------- инференс ----------
    checker_max_iterations: int = 9          # потолок tool-вызовов на чекера
    checker_parallel: bool = True            # чекеры в пуле потоков
    checker_concurrency: int = 3
    checker_tools: str = "web_search,web_read,code_execute"
    llm_fact_extraction: bool = True         # извлечение фактов моделью + regex-фолбэк
    max_facts_per_checker: int = 0           # 0 = без ограничения
    agent_temperature: float = 0.1
    arbiter_temperature: float = 0.0
    arbiter_raw_chars: int = 12000           # сколько первичных отчётов отдать арбитру
    summary_fallback_chars: int = 1200       # фрагмент отчёта, если JSON не найден
    max_tool_result_chars: int = 12000       # усечение результатов инструментов
    history_char_budget: int = 60000         # бюджет истории одного агента
    api_max_retries: int = 3
    api_timeout: float = 120.0

    # ---------- чанкинг и кеш ----------
    chunking_threshold: int = 2000
    chunk_sentences: int = 8
    chunk_overlap_sentences: int = 1
    cache_ttl_seconds: int = 3600
    cache_max_items: int = 500

    # ---------- MCP (резерв под скрапинг) ----------
    mcp_enabled: bool = False
    mcp_url: str = ""

    # ---------- системное ----------
    pipeline_version: str = "v3.1"
    log_file: str = "logs.txt"
    log_level: str = "INFO"                  # уровень консоли; файл всегда DEBUG
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 3
    system_version: str = "factcheck_v1.1"
    yandex_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    router_base_url: str = "https://routerai.ru/api/v1"
    skills_dir: Path = Path(".agents/skills")
    prompts_dir: Path = Path(".agents/prompts")

    # ---------- вычисляемые (PrivateAttr) ----------
    _checker_model_list: List[str] = PrivateAttr(default_factory=list)
    _checker_tools_list: List[str] = PrivateAttr(
        default_factory=lambda: ["web_search", "web_read", "code_execute"]
    )
    _arbiter: str = PrivateAttr(default="")
    _extractor: str = PrivateAttr(default="")
    _reader: str = PrivateAttr(default="")
    _model_router: str = PrivateAttr(default="")
    _model_agent: str = PrivateAttr(default="")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore",
    )

    @model_validator(mode="after")
    def _resolve_derived(self) -> "Settings":
        # --- модели провайдера ---
        is_yandex = self.default_provider == "yandex"
        self._model_router = self.yandex_model_router if is_yandex else self.router_model
        self._model_agent = self.yandex_model_agent if is_yandex else self.router_model

        # --- список чекеров ---
        models = [m.strip() for m in self.checker_models.split(",") if m.strip()]
        models = models[: self.num_checkers_max]
        if len(models) < self.num_checkers_min:
            raise ValueError(
                f"Нужно {self.num_checkers_min}–{self.num_checkers_max} модели-чекера "
                f"(CHECKER_MODELS в .env), получено {len(models)}"
            )
        self._checker_model_list = models

        # --- инструменты чекеров ---
        self._checker_tools_list = [t.strip() for t in self.checker_tools.split(",") if t.strip()]

        # --- зависимые роли ---
        self._arbiter = self.arbiter_model or self._model_agent
        self._extractor = self.extractor_model or self._model_router
        self._reader = self.reader_model or self._model_agent
        return self

    # ---------- провайдеры ----------

    @property
    def providers(self) -> Dict[str, ProviderSettings]:
        return {
            "yandex": ProviderSettings(
                base_url=self.yandex_base_url,
                api_key=self.yandex_api_key,
                uri_format=f"gpt://{self.yandex_folder_id}/{{model}}",
                display_name="Yandex AI Studio",
            ),
            "router": ProviderSettings(
                base_url=self.router_base_url,
                api_key=self.router_ai_key,
                uri_format="{model}",
                display_name="Router AI",
            ),
        }

    @property
    def provider(self) -> ProviderSettings:
        return self.providers[self.default_provider]

    # ---------- вычисляемые поля ----------

    @property
    def model_router(self) -> str:
        return self._model_router

    @property
    def model_agent(self) -> str:
        return self._model_agent

    @property
    def checker_model_list(self) -> List[str]:
        return self._checker_model_list

    @property
    def checker_tools_list(self) -> List[str]:
        return self._checker_tools_list

    @property
    def arbiter(self) -> str:
        return self._arbiter

    @property
    def extractor(self) -> str:
        return self._extractor

    @property
    def reader(self) -> str:
        return self._reader

    # ---------- утилиты ----------

    def build_model_uri(self, model: str) -> str:
        if model.startswith(("gpt://", "ds://")):
            return model
        return self.provider.uri_format.format(model=model)

    def get_current_date_context(self) -> str:
        now = datetime.now()
        return f"Сегодня {now.strftime('%d.%m.%Y')}, {now.strftime('%A')}."


@lru_cache()
def get_settings() -> Settings:
    return Settings()
