import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from src.matclaw.config.base_config import MatClawSettings
from src.matclaw.config.logging_config import configure_logging
from src.matclaw.matlab.matlab_bridge import MatlabBridge
from src.matclaw.memory.memory import MemoryStore
from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.debug.debug_agent import DebugAgent
from src.matclaw.watchdog import WatchdogService
from src.matclaw.core.rpi_executor import RPIExecutor
from src.matclaw.sentry import SentryWatchdog
from src.matclaw.sentry.sync_handler import SyncHandler
from src.matclaw.gateways import TelegramHandler
from src.matclaw.gateways.nl_router import resolve_skill_from_nl, route_nl_message
from src.matclaw.lab_journal import append_lab_journal
from src.matclaw.skills import list_skills, load_skill_logic
from src.matclaw.vision import analyze_plot
from datetime import datetime


logger = logging.getLogger(__name__)


class Daemon:
    """
    Main always-on MatClaw daemon with a background heartbeat.
    Keeps the MATLAB bridge warm and ensures core subsystems are alive.
    """

    def __init__(self, settings: MatClawSettings) -> None:
        self.settings = settings
        self._shutdown = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self.matlab_bridge = MatlabBridge(settings=self.settings.matlab)
        self.debug_agent = DebugAgent(
            matlab_bridge=self.matlab_bridge,
            settings=self.settings.debug,
        )
        self.matlab_bridge.set_debug_agent(self.debug_agent)
        self.memory_store = MemoryStore(settings=self.settings.memory)
        self.memory_manager = MemoryManager(persist_directory=".matclaw_chromadb")
        token = (self.settings.telegram.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")) if self.settings.telegram.enabled else None
        self.telegram = TelegramHandler(token=token, chat_id=self.settings.telegram.chat_id or os.environ.get("TELEGRAM_CHAT_ID"))
        self._pending_tasks: dict[int, dict[str, Any]] = {}
        self.rpi_executor = RPIExecutor(
            matlab_bridge=self.matlab_bridge,
            memory_manager=self.memory_manager,
            on_run_complete=self._on_skill_complete,
            hitl_threshold_seconds=float(self.settings.hitl.threshold_seconds),
            on_pending_hitl=self._on_pending_hitl if self.settings.hitl.enabled else None,
        )
        self.watchdog = WatchdogService(settings=self.settings.watchdog, memory_store=self.memory_store)
        self.sentry = SentryWatchdog(
            settings=self.settings.sentry,
            lab_settings=self.settings.lab_journal,
            executor=self.rpi_executor,
        )
        self.sync_handler = SyncHandler(settings=self.settings.sync)

    def _on_skill_complete(
        self, summary: str, file_path: Path, skill_name: str, artifact_paths: list[str]
    ) -> None:
        """When a skill/sentry run finishes, optionally analyze plot (Vision) then send summary and plot to Telegram."""
        image_path = next((p for p in artifact_paths if p.lower().endswith(".png")), None)
        analysis = ""
        if image_path and self.settings.vision.enabled:
            analysis = analyze_plot(
                image_path,
                provider=self.settings.vision.provider,
                api_key=self.settings.vision.api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
                model=self.settings.vision.model,
            )
            if analysis:
                summary = f"I've analyzed the plot: {analysis}\n\n{summary}"
                if self.memory_manager:
                    self.memory_manager.store_artifact(
                        f"vision:{Path(image_path).name}",
                        {"vision_summary": analysis, "image_path": image_path, "source_file": str(file_path)},
                        vector=None,
                    )
        if self.settings.telegram.enabled and self.telegram._token:
            self.telegram.send_alert(summary, image_path=image_path)

    def _on_pending_hitl(self, chat_id: int, plan_data: dict, skill_name: str, skill_kwargs: dict, message_text: str) -> None:
        self._pending_tasks[chat_id] = {"plan_data": plan_data, "skill_name": skill_name, "skill_kwargs": skill_kwargs}
        self.telegram.send_alert_with_buttons(message_text, chat_id=chat_id)

    def _hitl_response(self, chat_id: int, approved: bool) -> None:
        task = self._pending_tasks.pop(chat_id, None)
        if not task:
            return
        if approved:
            try:
                self.rpi_executor.execute(task["plan_data"], task["skill_name"], skill_kwargs=task["skill_kwargs"])
                self.telegram.send_alert("Proceeding with run.", chat_id=chat_id)
            except Exception as exc:
                self.telegram.send_alert("Run failed: " + str(exc), chat_id=chat_id)
        else:
            self.telegram.send_alert("Cancelled.", chat_id=chat_id)

    def _telegram_command(self, command: str, args: list[str], chat_id: int) -> str:
        """Handle /status, /audit, /report, /run, and yes/no for HITL."""
        cmd = command.lower().strip()
        if cmd in ("yes", "no"):
            if chat_id in self._pending_tasks:
                self._hitl_response(chat_id, cmd == "yes")
                return "Proceeding." if cmd == "yes" else "Cancelled."
            return "No pending approval request."
        if cmd == "/run":
            if not args:
                return "Usage: /run <skill_name> e.g. /run pid_optimizer"
            skill_name = args[0]
            result = self.rpi_executor.run_rpi(skill_name, chat_id=chat_id)
            if isinstance(result, dict) and result.get("pending"):
                return "Approval requested. Reply Yes or No (or use the buttons)."
            return str(getattr(result, "message", result))[:500] if not isinstance(result, dict) else "Done."
        if cmd == "/status":
            journal_path = Path(self.settings.lab_journal.path)
            if not journal_path.is_file():
                return "LAB_JOURNAL.md is empty or missing."
            lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
            last = lines[-5:] if len(lines) >= 5 else lines
            return "\n".join(last) or "(no entries)"
        if cmd == "/audit":
            mod = load_skill_logic("workspace_auditor")
            if not mod or not hasattr(mod, "run"):
                return "Workspace auditor skill not available."
            result = mod.run(self.matlab_bridge)
            if getattr(result, "success", False) and getattr(result, "data", None):
                data = result.data
                summary = data.get("summary") or data.get("message") or ""
                variables = data.get("variables") or []
                parts = [f"✅ {summary}"]
                for v in variables[:20]:
                    name = v.get("name", "?")
                    size_mb = (v.get("bytes") or 0) / 1e6
                    cls = v.get("class", "?")
                    parts.append(f"  • {name}: {size_mb:.2f} MB ({cls})")
                if len(variables) > 20:
                    parts.append(f"  ... and {len(variables) - 20} more")
                return "\n".join(parts)
            return getattr(result, "error", "Audit failed.") or "Audit failed."
        if cmd == "/report":
            mod = load_skill_logic("report_generator")
            if not mod or not hasattr(mod, "run"):
                return "Report generator skill not available."
            project_id = " ".join(args) if args else None
            result = mod.run(
                self.matlab_bridge,
                memory_manager=self.memory_manager,
                telegram_handler=self.telegram,
                project_id=project_id,
            )
            if getattr(result, "success", False):
                data = getattr(result, "data", None) or {}
                return f"✅ Report generated. {data.get('summary', '')} Sent to Telegram: {data.get('sent', False)}."
            return getattr(result, "error", "Report failed.") or "Report failed."
        if cmd == "/sync":
            if not self.settings.sync.enabled or self.settings.sync.mode != "rsync":
                return "Sync is disabled or not in rsync mode. Set MATCLAW_SYNC__ENABLED=1 and MATCLAW_SYNC__MODE=rsync."
            code, paths = self.sync_handler.run_rsync()
            if code == 0:
                return f"Synced {len(paths)} file(s) into {self.settings.sync.rsync_dest or 'data_in'}."
            return f"Rsync failed (code {code}). Check RSYNC_SOURCE and network."
        return "Unknown command. Use /status, /audit, /report [project_id], /run <skill_name>, or /sync."

    def _handle_nl_message(
        self, message: str, chat_id: int, buffer: list[dict[str, str]]
    ) -> tuple[str, list[dict[str, str]], Path | None]:
        """
        Handle natural language: route via LLM to run_skill, execute_code, or ask_question.
        Returns (reply, updated_buffer) with last 5 messages.
        """
        skills = list_skills()
        llm = self.settings.llm
        api_key = llm.api_key or os.environ.get("NVIDIA_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            err_msg = "Natural language requires NVIDIA_API_KEY, GOOGLE_API_KEY, or ANTHROPIC_API_KEY. Use /run <skill_name> for skills."
            new_buf = (buffer + [{"role": "user", "content": message}, {"role": "assistant", "content": err_msg}])[-5:]
            return (err_msg, new_buf, None)

        lab_context_str = ""
        try:
            from src.matclaw.llm.context_loader import load_lab_context, format_lab_context_for_prompt
            lab_ctx = load_lab_context(
                matlab_bridge=self.matlab_bridge,
                memory_manager=self.memory_manager,
                memory_n_results=3,
            )
            lab_context_str = format_lab_context_for_prompt(lab_ctx)
        except Exception:
            pass

        intent_result = route_nl_message(
            message,
            buffer,
            skills,
            api_key=api_key,
            model=llm.model,
            provider=llm.provider,
            lab_context=lab_context_str or None,
        )

        reply = ""
        image_path: Path | None = None
        journal_path = Path(self.settings.lab_journal.path) if self.settings.lab_journal.enabled else None

        if intent_result.intent == "run_skill":
            skill_name = resolve_skill_from_nl(intent_result.skill_name, skills)
            if skill_name:
                result = self.rpi_executor.run_rpi(skill_name, chat_id=chat_id)
                if isinstance(result, dict) and result.get("pending"):
                    reply = "Approval requested. Reply Yes or No (or use the buttons)."
                else:
                    reply = str(getattr(result, "message", result))[:500] if not isinstance(result, dict) else str(result)[:500]
                if journal_path:
                    append_lab_journal(journal_path, f"NL→{skill_name}: {reply[:100]}", source="Telegram", artifact_paths=[])
            else:
                reply = f"Could not match to a skill. Available: {', '.join(skills)}"
        elif intent_result.intent == "execute_code" and intent_result.matlab_code:
            success, output = self.matlab_bridge.run_matlab_code(intent_result.matlab_code)
            reply = output if success else f"MATLAB error: {output}"
            if journal_path:
                append_lab_journal(journal_path, f"NL→run_matlab_code: {'OK' if success else 'FAIL'}", source="Telegram", artifact_paths=[])

            # If MATLAB created a figure, capture it, run vision analysis, and store as Visual Insight
            if success and self.settings.vision.enabled:
                fig_dir = Path("reports/telegram_figures")
                fig_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fig_path = fig_dir / f"fig_{stamp}.png"
                if self.matlab_bridge.capture_figure(fig_path):
                    analysis = analyze_plot(
                        fig_path,
                        provider=self.settings.vision.provider,
                        api_key=self.settings.vision.api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
                        model=self.settings.vision.model,
                    )
                    if analysis:
                        reply = f"📊 **Visual Insight:**\n{analysis}\n\n{reply}".strip()
                        self.memory_manager.store_artifact(
                            f"visual_insight:{fig_path.stem}",
                            {
                                "vision_summary": analysis,
                                "image_path": str(fig_path.resolve()),
                                "source": "Telegram",
                                "user_request": message[:200],
                                "summary": analysis,
                                "skill": "execute_code",
                            },
                            vector=None,
                        )
                    image_path = fig_path
        elif intent_result.intent == "ask_question" or intent_result.query:
            results = self.memory_manager.query_context(intent_result.query or message, n_results=5)
            if results:
                parts = []
                for i, r in enumerate(results[:5], 1):
                    meta = r.get("metadata") or {}
                    doc = r.get("document") or meta.get("summary") or meta.get("message") or str(meta)[:200]
                    parts.append(f"{i}. {doc}")
                reply = "\n".join(parts)
            else:
                reply = "No relevant results in memory. Try running a skill first (e.g. 'Check my workspace')."
        else:
            reply = f"Understood intent: {intent_result.intent}. No action taken."

        new_buffer = buffer + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply},
        ]
        return reply, new_buffer[-5:], image_path

    def start(self) -> None:
        logger.info("Starting MatClaw daemon.")
        self._install_signal_handlers()

        # Initialize subsystems
        self.memory_store.initialize()
        self.matlab_bridge.start()
        self.watchdog.start()
        self.sentry.start()
        self.sync_handler.start_cloud_watch()
        if self.settings.telegram.enabled and self.telegram._token:
            self.telegram.start_listener(
                self._telegram_command,
                nl_handler=self._handle_nl_message,
                hitl_response_callback=self._hitl_response,
            )

        # Start heartbeat
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="matclaw-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

        logger.info("MatClaw daemon started. Entering main loop.")

        # Main loop can later host an event loop / job scheduler.
        try:
            while not self._shutdown.is_set():
                time.sleep(0.5)
        finally:
            self.stop()

    def stop(self) -> None:
        if self._shutdown.is_set():
            return
        logger.info("Stopping MatClaw daemon.")
        self._shutdown.set()
        try:
            self.matlab_bridge.stop()
        except Exception:
            logger.exception("Error while stopping MATLAB bridge.")
        try:
            self.watchdog.stop()
        except Exception:
            logger.exception("Error while stopping watchdog.")
        try:
            self.sentry.stop()
        except Exception:
            logger.exception("Error while stopping sentry.")
        try:
            self.sync_handler.stop_cloud_watch()
        except Exception:
            logger.exception("Error while stopping sync handler.")
        try:
            self.telegram.stop_listener()
        except Exception:
            logger.exception("Error while stopping Telegram listener.")
        try:
            self.memory_store.close()
        except Exception:
            logger.exception("Error while closing memory store.")
        logger.info("MatClaw daemon stopped.")

    def _heartbeat_loop(self) -> None:
        interval = self.settings.daemon.heartbeat_interval_seconds
        while not self._shutdown.is_set():
            try:
                logger.debug("Heartbeat tick.")
                # Lightweight health checks
                self._check_subsystems()
            except Exception:
                logger.exception("Error during heartbeat tick.")
            self._shutdown.wait(interval)

    def _check_subsystems(self) -> None:
        if not self.matlab_bridge.is_healthy():
            logger.warning("MATLAB bridge reports unhealthy state; attempting recover.")
            try:
                self.matlab_bridge.restart()
            except Exception:
                logger.exception("Failed to restart MATLAB bridge.")

        if not self.memory_store.is_healthy():
            logger.warning("Memory store reports unhealthy state.")

    def _install_signal_handlers(self) -> None:
        def handle_signal(signum, frame):  # type: ignore[no-untyped-def]
            logger.info("Received signal %s, shutting down daemon.", signum)
            self.stop()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)


@contextmanager
def run_daemon() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    settings = MatClawSettings()  # Loaded from env / defaults
    configure_logging(settings.logging)
    daemon = Daemon(settings=settings)
    try:
        yield daemon
    finally:
        daemon.stop()


if __name__ == "__main__":
    with run_daemon() as d:
        d.start()

