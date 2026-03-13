"""
Core orchestration: RPI executor and task routing.

RPIExecutor runs the Research → Plan → Execute loop, optionally with
MemoryManager context and lab journal logging.
"""

from .rpi_executor import RPIExecutor

__all__ = ["RPIExecutor"]
