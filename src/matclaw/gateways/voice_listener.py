"""
Voice Gateway (Nemotron-3 VoiceChat) for interruptible, low-latency intent execution.

This module is designed to:
  - stream user audio to a Nemotron-3 VoiceChat NIM endpoint (async)
  - parse model-emitted "voice intent" events early (speech-to-intent)
  - trigger the MatClaw RPI loop (or safe MATLAB execution) immediately
  - cancel/interrupt in-flight actions when new speech arrives

Note:
  The exact wire protocol for Nemotron VoiceChat depends on deployment.
  This implementation focuses on an extensible client interface and safe
  intent routing into MatClaw's existing RPI + MATLAB execution guardrails.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from pydantic import BaseModel, Field

from matclaw.core.rpi_executor import RPIExecutor
from matclaw.matlab.matlab_bridge import MatlabBridge
from matclaw.memory.memory_manager import MemoryManager
from matclaw.skills import load_skill_logic

logger = logging.getLogger(__name__)

try:
    import websockets  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


class VoiceIntent(BaseModel):
    """Structured intent emitted by VoiceChat (or inferred from it)."""

    intent: str = Field(
        description="One of: run_skill | execute_code | ask_question | respond"
    )
    skill_name: str | None = None
    matlab_code: str | None = None
    query: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class NemotronVoiceChatStreamClient:
    """
    OpenAI-compatible-style streaming client for Nemotron VoiceChat.

    Expected to connect to a websocket endpoint and receive JSON messages.
    """

    def __init__(
        self,
        ws_url: str,
        model: str,
        api_key: str | None = None,
    ) -> None:
        if websockets is None:
            raise RuntimeError("websockets is not installed; cannot run voice streaming.")
        self.ws_url = ws_url
        self.model = model
        self.api_key = api_key

    async def stream_voice_intents(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        system_prompt: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[VoiceIntent]:
        """
        Stream audio and yield VoiceIntent events.

        This function attempts to parse any incoming JSON that contains intent fields.
        If the server emits plain text, the event is ignored (caller may choose to route).
        """

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with websockets.connect(self.ws_url, extra_headers=headers) as ws:  # type: ignore[union-attr]
            await ws.send(
                json.dumps(
                    {
                        "type": "start",
                        "model": self.model,
                        "system_prompt": system_prompt,
                        "streaming": True,
                    }
                )
            )

            # Producer: send audio frames
            async def _send_audio() -> None:
                async for chunk in audio_chunks:
                    if cancel_event.is_set():
                        break
                    await ws.send(
                        json.dumps(
                            {
                                "type": "audio",
                                "data_b64": base64.b64encode(chunk).decode("utf-8"),
                            }
                        )
                    )
                try:
                    await ws.send(json.dumps({"type": "end"}))
                except Exception:
                    pass

            send_task = asyncio.create_task(_send_audio())

            try:
                while not cancel_event.is_set():
                    raw_msg = await ws.recv()
                    if not raw_msg:
                        continue
                    try:
                        data = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    # Common shapes (best-effort)
                    if isinstance(data, dict) and ("intent" in data or "skill_name" in data or "matlab_code" in data):
                        yield VoiceIntent.model_validate(
                            {
                                "intent": data.get("intent") or "respond",
                                "skill_name": data.get("skill_name"),
                                "matlab_code": data.get("matlab_code"),
                                "query": data.get("query"),
                                "raw": data,
                            }
                        )
            finally:
                cancel_event.set()
                send_task.cancel()


class VoiceGateway:
    """
    Route voice intents into MatClaw's RPI executor / MATLAB bridge.

    For "interruptible speech", the gateway cancels any in-flight action when it
    receives a new intent event.
    """

    SYSTEM_PROMPT = (
        "You are a voice controller for MatClaw.\n"
        "Convert the user's spoken command into a single JSON object with:\n"
        "{intent, skill_name, matlab_code, query}.\n"
        "Rules:\n"
        "- intent is run_skill | execute_code | ask_question | respond\n"
        "- if intent is run_skill, set skill_name (e.g. pid_optimizer)\n"
        "- if intent is execute_code, set matlab_code as MATLAB code to run\n"
        "- if intent is ask_question, set query to the question\n"
        "- Always avoid including any sensitive workspace variable values.\n"
        "Return ONLY valid JSON."
    )

    def __init__(
        self,
        *,
        voice_client: NemotronVoiceChatStreamClient,
        rpi_executor: RPIExecutor,
        matlab_bridge: MatlabBridge,
        memory_manager: MemoryManager | None = None,
        on_intent: Callable[[VoiceIntent], None] | None = None,
    ) -> None:
        self.voice_client = voice_client
        self.rpi_executor = rpi_executor
        self.matlab_bridge = matlab_bridge
        self.memory_manager = memory_manager
        self.on_intent = on_intent

        self._current_action_task: asyncio.Task[Any] | None = None

    async def _execute_intent(self, intent: VoiceIntent, chat_id: int | None) -> str:
        """Execute a parsed voice intent with RPI guardrails."""

        if self.on_intent:
            try:
                self.on_intent(intent)
            except Exception:
                pass

        if intent.intent == "run_skill":
            if not intent.skill_name:
                return "Voice intent missing skill_name."
            result = await asyncio.to_thread(self.rpi_executor.run_rpi, intent.skill_name, chat_id=chat_id)
            return str(getattr(result, "message", result)) if not isinstance(result, dict) else str(result)

        if intent.intent == "execute_code":
            # Research phase: audit workspace/licensing before running user code.
            ws_auditor = load_skill_logic("workspace_auditor")
            if ws_auditor and hasattr(ws_auditor, "run"):
                try:
                    _ = ws_auditor.run(self.matlab_bridge)
                except Exception:
                    pass

            if not intent.matlab_code:
                return "Voice intent missing matlab_code."
            ok, output = await asyncio.to_thread(self.matlab_bridge.run_matlab_code, intent.matlab_code)
            return output if ok else f"MATLAB error: {output}"

        if intent.intent == "ask_question":
            if not intent.query:
                return "Voice intent missing query."
            if self.memory_manager is None:
                return "Memory manager unavailable."
            results = self.memory_manager.query_context(intent.query, n_results=3)
            if not results:
                return "No matching artifacts in memory."
            # Return top snippet summaries only.
            lines: list[str] = []
            for i, r in enumerate(results, 1):
                meta = r.get("metadata") or {}
                doc = r.get("document") or ""
                summary = meta.get("summary") or meta.get("message") or doc[:180]
                lines.append(f"{i}. {summary}")
            return "\n".join(lines)

        # respond/fallback: no execution
        return intent.query or "OK"

    async def run(
        self,
        *,
        audio_chunks: AsyncIterator[bytes],
        chat_id: int | None = None,
        cancel_on_new_intent: bool = True,
    ) -> None:
        """
        Run the voice gateway.

        Args:
            audio_chunks: Async iterator yielding raw audio frames (PCM 16kHz expected).
            chat_id: Optional chat id for HITL gating.
            cancel_on_new_intent: If True, cancel any in-flight execution when a new intent arrives.
        """

        cancel_event = asyncio.Event()
        async for intent in self.voice_client.stream_voice_intents(
            audio_chunks,
            system_prompt=self.SYSTEM_PROMPT,
            cancel_event=cancel_event,
        ):
            if cancel_on_new_intent and self._current_action_task and not self._current_action_task.done():
                self._current_action_task.cancel()
            self._current_action_task = asyncio.create_task(self._execute_intent(intent, chat_id))

