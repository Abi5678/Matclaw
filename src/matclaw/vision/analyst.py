"""
Vision Analyst: analyze .png plots via Anthropic (or Google) vision API.

When a plot is generated, the analyst summarizes it (e.g. overshoot, oscillation)
and can suggest parameter changes (e.g. "increase Kd by 0.05 to dampen oscillation").
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore[misc, assignment]

DEFAULT_PROMPT = (
    "Analyze this engineering plot (e.g. step response, Bode, time series). "
    "Note: overshoot, rise time, settling, oscillation, or anomalies. "
    "If it looks like a control-system response, suggest a concrete parameter change (e.g. 'Increase derivative gain Kd by 0.05 to dampen the oscillation at 2.5s'). "
    "Keep the summary to 2-4 sentences."
)


def analyze_plot(
    image_path: str | Path,
    prompt: str | None = None,
    provider: str = "anthropic",
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """
    Send the image to a vision-capable LLM and return the analysis text.
    Uses Anthropic by default; Google can be added via provider="google".
    """
    path = Path(image_path)
    if not path.is_file():
        return ""
    prompt = prompt or DEFAULT_PROMPT
    if provider == "anthropic" and Anthropic is not None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            logger.warning("ANTHROPIC_API_KEY not set; skipping vision analysis.")
            return ""
        try:
            with path.open("rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            client = Anthropic(api_key=key)
            model_name = model or os.environ.get("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-20250514")
            msg = client.messages.create(
                model=model_name,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            text = msg.content[0].text if msg.content else ""
            return text.strip()
        except Exception as exc:
            logger.exception("Vision analysis (Anthropic) failed: %s", exc)
            return ""
    if provider == "google":
        try:
            from google import genai
            client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
            model_name = model or os.environ.get("GOOGLE_VISION_MODEL", "gemini-2.0-flash")
            with path.open("rb") as f:
                img_bytes = f.read()
            media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            r = client.models.generate_content(
                model=model_name,
                contents=[
                    genai.types.Part.from_bytes(data=img_bytes, mime_type=media_type),
                    prompt,
                ],
            )
            return (r.text or "").strip()
        except Exception as exc:
            logger.exception("Vision analysis (Google) failed: %s", exc)
            return ""
    return ""
