"""
Report Generator skill: compile lessons learned + latest plot into Markdown (or PDF) and send via Telegram.

Takes project_id (or query) from MemoryManager; no MATLAB required.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.skills.base import SkillResult

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent
_REPORTS_DIR = _SKILL_DIR / "reports"


def research(
    memory_manager: MemoryManager,
    project_id: str | None = None,
    query: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Query MemoryManager for lessons learned and any artifact metadata (e.g. plot paths)."""
    out: dict[str, Any] = {"lessons": [], "documents": [], "error": None}
    try:
        q = project_id or query or "recent project parameters"
        results = memory_manager.query_context(q, n_results=15)
        out["lessons"] = [r.get("metadata") or {} for r in results]
        out["documents"] = [r.get("document") for r in results]
    except Exception as exc:
        logger.exception("Report generator research failed: %s", exc)
        out["error"] = str(exc)
    return out


def plan(research_data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Decide report sections: title, lessons learned, latest plot reference, timestamp."""
    lessons = research_data.get("lessons") or []
    documents = research_data.get("documents") or []
    plan_data: dict[str, Any] = {
        "sections": [
            {"name": "Lessons learned", "items": lessons[:10]},
            {"name": "Summary", "items": documents[:5]},
        ],
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    return plan_data


def execute(
    plan_data: dict[str, Any],
    memory_manager: MemoryManager,
    telegram_handler: Any = None,
    project_id: str | None = None,
    output_dir: Path | str | None = None,
    **kwargs: Any,
) -> SkillResult:
    """Write Markdown report and send via Telegram send_document if handler provided."""
    try:
        out_dir = Path(output_dir or _REPORTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        proj = (project_id or "report").replace(" ", "_")[:50]
        report_path = out_dir / f"matclaw_report_{proj}_{stamp}.md"

        sections = plan_data.get("sections") or []
        generated_at = plan_data.get("generated_at", "")

        lines = [
            "# MatClaw Project Report",
            "",
            f"**Generated:** {generated_at}",
            f"**Project:** {project_id or 'General'}",
            "",
            "---",
            "",
        ]
        for sec in sections:
            name = sec.get("name", "Section")
            items = sec.get("items") or []
            lines.append(f"## {name}")
            lines.append("")
            for i, item in enumerate(items, 1):
                if isinstance(item, dict):
                    summary = item.get("summary") or item.get("message") or item.get("document") or str(item)[:200]
                    lines.append(f"{i}. {summary}")
                else:
                    lines.append(f"{i}. {item}")
            lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        summary = f"Report with {len(sections)} sections, {sum(len(s.get('items', [])) for s in sections)} items."

        sent = False
        if telegram_handler is not None and getattr(telegram_handler, "send_document", None):
            sent = telegram_handler.send_document(report_path, caption=summary)

        return SkillResult(
            success=True,
            message=summary,
            data={"report_path": str(report_path), "sent": sent, "summary": summary},
        )
    except Exception as exc:
        logger.exception("Report generator execute failed: %s", exc)
        return SkillResult(success=False, error=str(exc))


def run(
    matlab_bridge: Any,
    memory_manager: MemoryManager | None = None,
    telegram_handler: Any = None,
    project_id: str | None = None,
    query: str | None = None,
    output_dir: Path | str | None = None,
    **kwargs: Any,
) -> SkillResult:
    """
    One-shot: research (query memory) → plan → execute (write .md, send via Telegram).
    Creates its own MemoryManager if none provided.
    """
    mm = memory_manager
    if mm is None:
        try:
            mm = MemoryManager()
            mm._ensure_client()
        except Exception as exc:
            logger.warning("Could not init MemoryManager: %s — report will have no history.", exc)
            mm = None
    r = research(mm, project_id=project_id, query=query, **kwargs) if mm else {"lessons": [], "documents": [], "error": None}
    p = plan(r, **kwargs)
    return execute(p, memory_manager=mm, telegram_handler=telegram_handler, project_id=project_id, output_dir=output_dir, **kwargs)
