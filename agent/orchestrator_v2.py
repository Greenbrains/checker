"""
FactcheckOrchestratorV2 — пайплайн claims: extract → dispatch → verify → arbitrate.
Version: 2.4.0
Description:
- экстрактор (skill fact-extract) достаёт claims в JSON;
- dispatcher (claims.py) группирует claims в кластеры по типу/источнику;
- верификаторы: verify-web (finder) → source_cache → verify-read; verify-math; verify-grammar;
- арбитр (skill fact-arbiter) сводит вердикты в итоговый check.md.
source_cache: url → {"text", "ts"} — источник читается один раз.
Изменения 2.4.0: utils.py переименован в claims.py; _tools() делегирует фильтрацию
    в registry.get_tools_by_names() вместо ручного перебора all_tools.
"""
import json
import logging
import time
from typing import Dict, List

from agent.base import BaseAgent, UsageTracker
from agent.core.prompts.loader import PromptLoader
from agent.core.readers.input_reader import build_user_message, load_input_documents
from agent.core.tools.agent_tools import load_skill
from agent.core.tools.registry import ToolRegistry
from agent.core.tools.claims import dispatch, parse_json

logger = logging.getLogger("agent.orchestrator_v2")

# Разрешённые инструменты для каждой роли v2
V2_TOOLSETS: Dict[str, set] = {
    "fact-extract":   set(),
    "verify-web":     {"web_search", "web_read"},
    "verify-read":    set(),
    "verify-math":    {"code_execute"},
    "verify-grammar": set(),
    "fact-arbiter":   set(),
}

# Индекс модели в settings.checker_model_list для каждой роли
ROLE_MODEL_IDX: Dict[str, int] = {
    "fact-extract":   0,
    "verify-web":     0,
    "verify-read":    1,
    "verify-math":    1,
    "verify-grammar": 1,
}

MAX_CACHE_CHARS = 8000
MAX_CACHE_SOURCES_PER_CLUSTER = 3


class FactcheckOrchestratorV2:
    def __init__(self, client, settings, registry: ToolRegistry):
        self.client = client
        self.settings = settings
        self.registry = registry
        self.prompt_loader = PromptLoader()
        self.usage = UsageTracker()
        self.source_cache: Dict[str, dict] = {}
        self.out = settings.output_dir
        self.out.mkdir(parents=True, exist_ok=True)

    # ---------- инфраструктура ----------

    def _tools(self, skill_name: str):
        allowed = V2_TOOLSETS.get(skill_name, set())
        return self.registry.get_tools_by_names(allowed)

    def _system(self, skill_name: str) -> str:
        return self.prompt_loader.render_system_prompt(skill_context=load_skill(skill_name))

    def _model_for(self, skill_name: str) -> str:
        models = self.settings.checker_model_list
        return models[ROLE_MODEL_IDX.get(skill_name, 0) % len(models)]

    def _agent(self, skill_name: str, role: str, temperature: float = 0.1) -> BaseAgent:
        schema, router = self._tools(skill_name)
        return BaseAgent(
            client=self.client,
            model=self._model_for(skill_name),
            system_prompt=self._system(skill_name),
            tools_schema=schema,
            tool_router=router,
            usage_tracker=self.usage,
            role_name=role,
            temperature=temperature,
        )

    def _run_json(self, skill_name: str, role: str, user_msg: str,
                  max_iterations: int = 4) -> dict:
        agent = self._agent(skill_name, role)
        raw = agent.run(user_msg, max_iterations=max_iterations)
        data = parse_json(raw)
        if data is None:
            raw = agent.run(
                user_msg + "\n\n[Предыдущая попытка не дала валидный JSON. "
                           "Верни ТОЛЬКО валидный JSON строго по схеме из системного промпта.]",
                max_iterations=max_iterations,
            )
            data = parse_json(raw)
        return data or {"verdicts": [], "parse_error": (raw or "")[:500]}

    @staticmethod
    def _cluster_msg(claims: List[dict]) -> str:
        return (
            "CLAIMS кластера:\n```json\n"
            + json.dumps(claims, ensure_ascii=False, indent=2)
            + "\n```"
        )

    # ---------- source_cache ----------

    def _cache_fetch(self, url: str) -> str:
        if url in self.source_cache:
            return self.source_cache[url]["text"]
        if "wikipedia.org" in url:
            logger.warning(f"⛔ Wikipedia заблокирована, пропускаю: {url}")
            return ""
        fn = self.registry.router.get("web_read")
        if fn is None:
            logger.error("❌ web_read не найден в registry.router")
            return ""
        try:
            text = str(fn(url=url, max_chars=MAX_CACHE_CHARS))
        except Exception as e:
            text = f"❌ Ошибка загрузки: {e}"
        if text.startswith("❌"):
            logger.warning(f"⚠️ {url} недоступен: {text[:100]}")
            return ""
        self.source_cache[url] = {"text": text, "ts": time.time()}
        logger.info(f"🗂 source_cache += {url} ({len(text)} симв.)")
        return text

    # ---------- стадии ----------

    def _extract(self, material_msg: str) -> List[dict]:
        data = self._run_json("fact-extract", "extractor", material_msg, max_iterations=2)
        claims = data.get("claims", [])
        for i, c in enumerate(claims, 1):
            c.setdefault("id", i)
        logger.info(f"🧬 Извлечено claims: {len(claims)}")
        return claims

    def _run_web_cluster(self, key: str, claims: List[dict]) -> tuple:
        finder = self._run_json(
            "verify-web", f"finder:{key}", self._cluster_msg(claims), max_iterations=6
        )
        urls: List[str] = []
        for v in finder.get("verdicts", []):
            for u in v.get("sources") or []:
                if isinstance(u, str) and u.startswith("http") and u not in urls:
                    urls.append(u)

        cached = 0
        for u in urls:
            if cached >= MAX_CACHE_SOURCES_PER_CLUSTER:
                break
            if self._cache_fetch(u):
                cached += 1

        texts = "\n\n".join(
            f"=== ИСТОЧНИК {u} ===\n{self.source_cache[u]['text']}"
            for u in urls if u in self.source_cache
        ) or "(источники недоступны)"

        reader = self._run_json(
            "verify-read", f"reader:{key}",
            self._cluster_msg(claims) + "\n\nТЕКСТЫ ИСТОЧНИКОВ:\n" + texts,
            max_iterations=2,
        )
        return finder, reader

    def _arbitrate(self, material_text: str, claims: List[dict],
                   verdicts: List[dict], consistency: List[dict]) -> str:
        agent = BaseAgent(
            client=self.client,
            model=self.settings.arbiter,
            system_prompt=self._system("fact-arbiter"),
            usage_tracker=self.usage,
            role_name="arbiter",
            temperature=0.1,
        )
        payload = {
            "material": material_text,
            "claims": claims,
            "verdicts": verdicts,
            "consistency_claims": consistency,
            "source_cache_urls": list(self.source_cache.keys()),
        }
        msg = (
            "Сведи вердикты верификаторов в итоговый отчёт по формату навыка.\n\n"
            "ДАННЫЕ:\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n```"
        )
        return agent.run(msg, max_iterations=2)

    # ---------- главный вход ----------

    def run_batch(self) -> str:
        docs = load_input_documents(self.settings.input_dir)
        material_text = build_user_message(docs, images=False)
        try:
            material_mm = build_user_message(docs, images=True)
        except Exception:
            material_mm = material_text
        logger.info(f"🧬 [pipeline v2] материалов: {len(docs)}")

        # 1. экстракция
        claims = self._extract(material_mm)
        (self.out / "v2_claims.json").write_text(
            json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 2. dispatch + верификация
        verdicts: List[dict] = []
        for cl in dispatch(claims):
            logger.info(
                f"=== Кластер {cl['key']} → {cl['skill']} ({len(cl['claims'])} claims) ==="
            )
            if cl["skill"] == "verify-web":
                finder, reader = self._run_web_cluster(cl["key"], cl["claims"])
                for src, data in (("finder", finder), ("reader", reader)):
                    for v in data.get("verdicts", []):
                        v["role"], v["cluster"] = src, cl["key"]
                        verdicts.append(v)
            else:
                iters = 4 if cl["skill"] == "verify-math" else 2
                data = self._run_json(
                    cl["skill"], cl["skill"],
                    self._cluster_msg(cl["claims"]),
                    max_iterations=iters,
                )
                for v in data.get("verdicts", []):
                    v["role"], v["cluster"] = cl["skill"], cl["key"]
                    verdicts.append(v)

        (self.out / "v2_verdicts.json").write_text(
            json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 3. арбитраж
        consistency = [c for c in claims if c.get("type") == "consistency"]
        final = self._arbitrate(material_text, claims, verdicts, consistency)
        full = (
            final
            + "\n\n---\n\n# Приложение A. Claims (fact-extract)\n```json\n"
            + json.dumps(claims, ensure_ascii=False, indent=2)
            + "\n```\n\n# Приложение B. Вердикты верификаторов\n```json\n"
            + json.dumps(verdicts, ensure_ascii=False, indent=2)
            + "\n```\n\n# Приложение C. Source cache\n"
            + ("\n".join(f"- {u}" for u in self.source_cache) or "(пусто)")
        )
        (self.out / "check.md").write_text(full, encoding="utf-8")
        logger.info(f"💾 Итоговый отчёт: {self.out / 'check.md'}")
        logger.info(f"\n{self.usage.summary()}")
        return full
