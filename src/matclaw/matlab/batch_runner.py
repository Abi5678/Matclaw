"""
MatClaw Batch Runner — executes MATLAB scripts in isolated subprocesses.

This is the robust path for complex simulations. Each run:
- Spawns a fresh `matlab -batch` process (isolated, can't crash the desktop)
- Captures stdout/stderr cleanly
- Has a hard timeout that kills the process (not the shared engine)
- Returns plots saved to the shared plots directory

Usage falls back to the shared engine for fast/simple calls;
the batch runner is used for multi-second simulations.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Prefer R2025b, fall back to R2023a
_MATLAB_CANDIDATES = [
    "/Applications/MATLAB_R2025b.app/bin/matlab",
    "/Applications/MATLAB_R2024b.app/bin/matlab",
    "/Applications/MATLAB_R2024a.app/bin/matlab",
    "/Applications/MATLAB_R2023b.app/bin/matlab",
    "/Applications/MATLAB_R2023a.app/bin/matlab",
]

def _find_matlab() -> str | None:
    for p in _MATLAB_CANDIDATES:
        if Path(p).exists():
            return p
    return None

MATLAB_BIN = _find_matlab()


def run_batch(
    code: str,
    plots_dir: str,
    timeout: float = 240.0,
    plot_name: str | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Run MATLAB code in an isolated subprocess via `matlab -batch`.

    Args:
        code:       MATLAB code to execute
        plots_dir:  Directory to save PNG plots into
        timeout:    Max seconds before the process is killed (default 4 min)
        plot_name:  Base name for the saved plot file

    Returns:
        (success, output_text, plot_urls)
    """
    if MATLAB_BIN is None:
        return False, "MATLAB not found. Ensure MATLAB R2023a+ is installed.", []

    ts = int(time.time())
    plot_base = plot_name or f"plot_{ts}"
    save_path = str(Path(plots_dir) / f"{plot_base}.png").replace("\\", "/")
    plots: list[str] = []

    # Build the full script with headless figure save
    full_code = _build_script(code, save_path)

    with tempfile.NamedTemporaryFile(
        suffix=".m", delete=False, mode="w", encoding="utf-8", errors="replace"
    ) as f:
        f.write(full_code)
        script_path = f.name

    try:
        result = subprocess.run(
            [
                MATLAB_BIN,
                "-nosplash",       # Skip splash screen
                "-nodesktop",      # Don't initialize desktop
                "-batch",          # Batch mode: exit with error code on failure
                f"run('{script_path.replace(chr(39), chr(39)*2)}');",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "MATLABPATH": plots_dir},
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Filter MATLAB startup noise from output
        stdout = _clean_output(stdout)

        # Check if plot was saved
        if Path(save_path).exists() and Path(save_path).stat().st_size > 0:
            plots.append(f"/plots/{Path(save_path).name}")

        # Also scan for any other PNGs created during the run
        plots_dir_path = Path(plots_dir)
        before_ts = ts - 1
        for p in sorted(plots_dir_path.glob("*.png"), key=lambda x: x.stat().st_mtime):
            if p.stat().st_mtime >= before_ts and f"/plots/{p.name}" not in plots:
                plots.append(f"/plots/{p.name}")

        if result.returncode == 0:
            output = stdout if stdout else f"Simulation completed successfully."
            return True, output, plots
        else:
            # MATLAB -batch exits with code 1 on error
            error_text = _extract_matlab_error(stderr or stdout)
            return False, f"MATLAB error: {error_text}", plots

    except subprocess.TimeoutExpired:
        logger.error("MATLAB batch timed out after %.0fs", timeout)
        return False, (
            f"Simulation timed out after {timeout:.0f}s. "
            "Try: fewer time steps, larger dt, or vectorized operations instead of for-loops."
        ), []
    except Exception as exc:
        logger.exception("Batch runner failed: %s", exc)
        return False, f"Batch runner error: {exc}", []
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _build_script(code: str, save_path: str) -> str:
    """Wrap user code with headless figure capture boilerplate."""
    return (
        "% MatClaw batch execution — headless mode\n"
        "set(0, 'DefaultFigureVisible', 'off');\n"
        "set(0, 'DefaultFigureRenderer', 'painters');\n"
        "\n"
        + code
        + f"\n\n"
        "% Auto-save any open figures\n"
        "try\n"
        "  figs = get(0, 'Children');\n"
        f"  if ~isempty(figs)\n"
        f"    exportgraphics(figs(1), '{save_path}', 'Resolution', 150);\n"
        f"    fprintf('Plot saved: {save_path}\\n');\n"
        "  end\n"
        "catch ME\n"
        "  try\n"
        f"    print(gcf, '-dpng', '-r150', '{save_path}');\n"
        "  catch\n"
        "  end\n"
        "end\n"
        "close all;\n"
    )


def _clean_output(text: str) -> str:
    """Remove MATLAB startup messages and license noise from output."""
    lines = text.splitlines()
    cleaned = []
    skip_patterns = [
        r"^MATLAB is selecting",
        r"^Please wait",
        r"^\s*$",
        r"^Licensing checkout",
        r"^Academic use",
    ]
    for line in lines:
        if any(re.match(pat, line, re.IGNORECASE) for pat in skip_patterns):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_matlab_error(text: str) -> str:
    """Pull the key error line from MATLAB's -batch error output."""
    # Look for "Error: ..." or "Undefined function..." style messages
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(Error|Undefined|Unrecognized|Unexpected)", line, re.IGNORECASE):
            return line[:300]
    # Fallback: return last non-empty line
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:300]
    return text[:300] if text else "Unknown error"


__all__ = ["run_batch", "MATLAB_BIN"]
