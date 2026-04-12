"""
Memory consolidation engine for MatClaw long-term knowledge.

Two consolidation modes:

1. **Automatic (no LLM)** — ``auto_extract_lessons()`` runs after every
   successful experiment.  It compares the latest run with the previous run
   for the same skill and stores simple metric-delta lessons.

2. **Deep (LLM-powered)** — ``consolidate_skill()`` fetches the last N
   experiments for a skill, sends them to an LLM, and stores the resulting
   strategies and insights in the :class:`KnowledgeBase`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from matclaw.config.base_config import LLMSettings
    from matclaw.core.experiment import ExperimentRecord, ExperimentTracker
    from matclaw.memory.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts for LLM-powered consolidation
# ---------------------------------------------------------------------------

CONSOLIDATION_SYSTEM = """You are a senior control-systems engineer analysing
experimental results for an autonomous MATLAB lab assistant called MatClaw.

Given a list of experiments (parameters, metrics, status), extract:
1. **lessons** — cause→effect observations (e.g. "Lowering Kd reduced oscillation").
2. **strategies** — higher-level approaches worth remembering (e.g. "For motor_v2, aggressive P with low D works best").
3. **failures** — patterns that should be avoided (e.g. "P > 2.0 causes instability").

Respond with ONLY JSON:
{
  "lessons": [{"text": "...", "cause": "...", "effect": "..."}],
  "strategies": [{"text": "...", "conditions": "..."}],
  "failures": [{"text": "...", "params": "...", "error": "..."}]
}

Keep each entry concise (1-2 sentences).  Prefer concrete numbers.
""".strip()


def _format_experiments_for_prompt(records: list[ExperimentRecord]) -> str:
    """Format experiment records into a readable prompt block."""
    lines: list[str] = []
    for i, r in enumerate(records, 1):
        line = (
            f"{i}. [{r.status}] {r.started_at.isoformat()[:10]} "
            f"params={json.dumps(r.params)} metrics={json.dumps(r.metrics)}"
        )
        if r.error:
            line += f" error={r.error[:120]}"
        if r.duration_seconds is not None:
            line += f" duration={r.duration_seconds:.1f}s"
        lines.append(line)
    return "\n".join(lines)


class ConsolidationEngine:
    """
    Consolidate raw experiment data into higher-level knowledge.

    Accepts an :class:`ExperimentTracker` for data and a :class:`KnowledgeBase`
    for storing generated insights.
    """

    def __init__(
        self,
        experiment_tracker: ExperimentTracker,
        knowledge_base: KnowledgeBase,
        llm_provider: str = "",
        llm_model: str = "",
        llm_api_key: str | None = None,
    ) -> None:
        self._tracker = experiment_tracker
        self._kb = knowledge_base
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._llm_api_key = llm_api_key

    # ------------------------------------------------------------------
    # Lightweight (no LLM)
    # ------------------------------------------------------------------

    def auto_extract_lessons(self, experiment_id: str) -> list[str]:
        """
        Compare the experiment with the previous run for the same skill and
        store simple metric-delta lessons.  No LLM call required.

        Returns the list of generated lesson texts (may be empty).
        """
        rec = self._tracker.get_experiment(experiment_id)
        if rec is None or rec.status != "success":
            return []

        # Get the last 2 experiments for this skill (most recent first)
        recent = self._tracker.list_experiments(skill_name=rec.skill_name, last_n=2)
        if len(recent) < 2:
            return []

        current = recent[0] if recent[0].experiment_id == experiment_id else recent[1]
        previous = recent[1] if recent[0].experiment_id == experiment_id else recent[0]

        lessons: list[str] = []

        # Compare metrics
        for key in current.metrics:
            cur_val = current.metrics.get(key)
            prev_val = previous.metrics.get(key)
            if not isinstance(cur_val, (int, float)) or not isinstance(prev_val, (int, float)):
                continue
            if prev_val == 0:
                continue
            delta_pct = ((cur_val - prev_val) / abs(prev_val)) * 100

            if abs(delta_pct) < 1.0:
                continue  # negligible change

            # Identify which parameters changed
            param_changes: list[str] = []
            for pk in set(current.params) | set(previous.params):
                cv = current.params.get(pk)
                pv = previous.params.get(pk)
                if cv != pv:
                    param_changes.append(f"{pk}: {pv}→{cv}")

            direction = "improved" if delta_pct < 0 else "worsened"
            # For metrics where lower is better (settling_time, error, cost)
            # 'improved' = decreased.  We keep it simple: just report the direction.
            lesson = (
                f"{key} {direction} by {abs(delta_pct):.1f}% "
                f"({prev_val}→{cur_val})"
            )
            if param_changes:
                lesson += f" when {', '.join(param_changes)}"

            lessons.append(lesson)
            self._kb.store_lesson(
                skill_name=rec.skill_name,
                lesson_text=lesson,
                cause=", ".join(param_changes) if param_changes else "unknown",
                effect=f"{key} {direction} by {abs(delta_pct):.1f}%",
                confidence=min(1.0, abs(delta_pct) / 100),
            )

        if lessons:
            logger.info(
                "Auto-extracted %d lessons for experiment %s (%s)",
                len(lessons), experiment_id, rec.skill_name,
            )

        return lessons

    # ------------------------------------------------------------------
    # Deep (LLM-powered)
    # ------------------------------------------------------------------

    def consolidate_skill(
        self,
        skill_name: str,
        since: datetime | None = None,
        max_experiments: int = 50,
    ) -> list[str]:
        """
        Run LLM-powered consolidation for *skill_name*.

        Fetches experiments since the last consolidation (or *since*), sends
        them to the configured LLM, and stores the resulting lessons, strategies,
        and failure notes in the knowledge base.

        Returns the list of generated insight/lesson texts.
        """
        # Determine the time boundary
        if since is None:
            last = self._tracker.last_consolidation(skill_name)
            if last:
                since = datetime.fromisoformat(last["consolidated_at"])
            else:
                since = datetime(2000, 1, 1, tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        records = self._tracker.list_experiments_in_range(since, now, skill_name=skill_name)
        if not records:
            logger.info("No new experiments to consolidate for %s", skill_name)
            return []

        records = records[:max_experiments]

        # Try LLM consolidation
        generated: list[str] = []
        try:
            generated = self._llm_consolidate(skill_name, records)
        except Exception:
            logger.exception("LLM consolidation failed for %s; falling back to heuristic", skill_name)
            # Fallback: just run auto_extract on each experiment
            for r in records:
                generated.extend(self.auto_extract_lessons(r.experiment_id))

        # Log consolidation
        self._tracker.log_consolidation(
            skill_name=skill_name,
            experiments_processed=len(records),
            insights_generated=len(generated),
            last_experiment_id=records[0].experiment_id if records else "",
        )

        return generated

    def consolidate_all(self) -> dict[str, list[str]]:
        """Run consolidation for all skills that have new data."""
        all_records = self._tracker.list_experiments(last_n=500)
        skill_names = {r.skill_name for r in all_records}
        results: dict[str, list[str]] = {}
        for skill in skill_names:
            insights = self.consolidate_skill(skill)
            if insights:
                results[skill] = insights
        return results

    def _llm_consolidate(
        self,
        skill_name: str,
        records: list[ExperimentRecord],
    ) -> list[str]:
        """Call the LLM to extract lessons, strategies, and failures."""
        from matclaw.llm.llm_client import call_chat_completion, resolve_api_key

        provider = self._llm_provider
        model = self._llm_model
        key = self._llm_api_key or resolve_api_key(provider) if provider else None

        if not provider or not key:
            logger.info("Consolidation LLM not configured; skipping deep consolidation for %s", skill_name)
            return []

        user_prompt = (
            f"Skill: {skill_name}\n"
            f"Experiments ({len(records)} total):\n"
            f"{_format_experiments_for_prompt(records)}"
        )

        text = call_chat_completion(
            provider=provider,
            model=model,
            system=CONSOLIDATION_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            api_key=key,
            max_tokens=1500,
        )

        # Parse JSON
        payload = self._parse_json(text)
        generated: list[str] = []

        for item in payload.get("lessons") or []:
            t = item.get("text") or str(item)
            self._kb.store_lesson(
                skill_name=skill_name,
                lesson_text=t,
                cause=item.get("cause", ""),
                effect=item.get("effect", ""),
            )
            generated.append(t)

        for item in payload.get("strategies") or []:
            t = item.get("text") or str(item)
            self._kb.store_strategy(
                skill_name=skill_name,
                strategy_text=t,
                conditions=item.get("conditions", ""),
            )
            generated.append(t)

        for item in payload.get("failures") or []:
            t = item.get("text") or str(item)
            self._kb.store_failure(
                skill_name=skill_name,
                description=t,
                error=item.get("error", ""),
            )
            generated.append(t)

        logger.info(
            "LLM consolidation for %s: %d lessons, %d strategies, %d failures",
            skill_name,
            len(payload.get("lessons") or []),
            len(payload.get("strategies") or []),
            len(payload.get("failures") or []),
        )
        return generated

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Best-effort JSON extraction from LLM response."""
        clean = text.strip()
        if "```" in clean:
            start = clean.find("```")
            if "json" in clean[start: start + 10].lower():
                start = clean.find("\n", start) + 1
            end = clean.find("```", start)
            if end > start:
                clean = clean[start:end]
        i = clean.find("{")
        j = clean.rfind("}")
        if i >= 0 and j > i:
            clean = clean[i: j + 1]
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("Consolidation LLM JSON parse failed.")
            return {}
