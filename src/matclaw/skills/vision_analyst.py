"""
VisionAnalyst: capture MATLAB figures and interpret plots via a local NVIDIA Multimodal NIM.

Uses OpenAI-compatible ``/v1/chat/completions`` on the local NIM (default http://localhost:8000).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.matclaw.matlab.matlab_bridge import MatlabBridge

logger = logging.getLogger(__name__)

TEMP_PLOT_NAME = "temp_plot.png"

_SYSTEM_PROMPT = (
    "Analyze this engineering plot. Extract: Overshoot, Settling Time, and Steady-State Error. "
    "Identify oscillations. Return JSON."
)


class VisionAnalysisResult(BaseModel):
    """Structured output from the multimodal NIM for control-style plots."""

    overshoot: float | None = Field(default=None, description="Overshoot (percent or absolute per plot).")
    settling_time: float | None = Field(default=None, description="Settling time in seconds if applicable.")
    steady_state_error: float | None = Field(default=None, description="Steady-state error magnitude.")
    oscillations: bool = Field(default=False, description="True if sustained/limit-cycle oscillations visible.")
    raw_text: str = Field(default="", description="Raw model text before JSON parse (if any).")


class VisionAnalyst:
    """
    Interprets MATLAB plots using a local NVIDIA Multimodal NIM.

    Args:
        matlab_bridge: Active bridge used to save the current figure via ``saveas``.
    """

    def __init__(
        self,
        matlab_bridge: MatlabBridge,
        *,
        nim_base_url: str | None = None,
        nim_model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._bridge = matlab_bridge
        self._nim_base_url = (nim_base_url or os.environ.get("MATCLAW_VISION_NIM_URL") or "http://localhost:8000/v1").rstrip(
            "/"
        )
        if not self._nim_base_url.endswith("/v1"):
            self._nim_base_url = f"{self._nim_base_url}/v1"
        self._nim_model = nim_model or os.environ.get("MATCLAW_VISION_NIM_MODEL", "nvidia/nemotron-nano-12b-v2-vl")
        self._api_key = api_key or os.environ.get("NVIDIA_API_KEY") or os.environ.get("OPENAI_API_KEY") or "local-not-needed"

    def _temp_plot_path(self) -> Path:
        return Path.cwd() / TEMP_PLOT_NAME

    def capture_and_analyze(self) -> VisionAnalysisResult | None:
        """
        Save the current figure to ``temp_plot.png``, call the local NIM, parse JSON, then delete the temp file.

        Returns:
            ``VisionAnalysisResult`` on success, or ``None`` if MATLAB or NIM fails (temp file still removed when created).
        """
        path = self._temp_plot_path()
        try:
            if not self._bridge.is_healthy():
                logger.warning("VisionAnalyst: MATLAB bridge not healthy; skipping capture.")
                return None

            # User-specified MATLAB: saveas(gcf, 'temp_plot.png') in cwd
            ok, err = self._bridge.run_matlab_code(
                "try; f = gcf; if ~isempty(f) && isvalid(f); saveas(f, 'temp_plot.png'); end; catch; end"
            )
            if not ok:
                logger.warning("VisionAnalyst: MATLAB saveas failed: %s", err)
                return None
            if not path.is_file():
                logger.info("VisionAnalyst: no figure saved (temp file missing).")
                return None

            png_bytes = path.read_bytes()
            return self._post_nim_and_parse(png_bytes)
        finally:
            try:
                if path.is_file():
                    os.remove(path)
            except OSError as exc:
                logger.debug("VisionAnalyst: could not remove temp plot: %s", exc)

    def analyze_png_file(self, image_path: str | Path) -> VisionAnalysisResult | None:
        """
        Analyze an existing PNG (e.g. artifact path) without MATLAB capture.

        Args:
            image_path: Path to a PNG on disk.

        Returns:
            Parsed ``VisionAnalysisResult`` or ``None`` on failure.
        """
        p = Path(image_path).expanduser().resolve()
        if not p.is_file():
            return None
        try:
            return self._post_nim_and_parse(p.read_bytes())
        except Exception as exc:
            logger.exception("VisionAnalyst analyze_png_file failed: %s", exc)
            return None

    def _post_nim_and_parse(self, png_bytes: bytes) -> VisionAnalysisResult | None:
        try:
            from openai import OpenAI
        except ImportError:
            logger.error("openai package required for VisionAnalyst NIM calls.")
            return None

        b64 = base64.b64encode(png_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        client = OpenAI(base_url=self._nim_base_url, api_key=self._api_key)
        try:
            resp = client.chat.completions.create(
                model=self._nim_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": "Return only valid JSON with keys: overshoot, settling_time, steady_state_error, oscillations (boolean). Use null for unknown numerics."},
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=512,
            )
            text = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.exception("VisionAnalyst: NIM request failed: %s", exc)
            return None

        try:
            return _parse_vision_json(text)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("VisionAnalyst: JSON parse failed: %s — text=%s", exc, text[:200])
            return VisionAnalysisResult(oscillations=False, raw_text=text[:2000])


def _parse_vision_json(text: str) -> VisionAnalysisResult:
    """Extract JSON object from model output and validate."""
    t = text.strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
        if m:
            t = m.group(1).strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i : j + 1]
    data: dict[str, Any] = json.loads(t)
    # tolerate alternate key names
    osc = data.get("oscillations")
    if isinstance(osc, str):
        osc = osc.lower() in ("true", "yes", "1")
    return VisionAnalysisResult(
        overshoot=_to_float(data.get("overshoot")),
        settling_time=_to_float(data.get("settling_time")),
        steady_state_error=_to_float(data.get("steady_state_error") or data.get("steadyStateError")),
        oscillations=bool(osc) if osc is not None else False,
        raw_text=text[:2000],
    )


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
