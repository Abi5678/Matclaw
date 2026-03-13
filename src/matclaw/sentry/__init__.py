"""
Proactive Sentry: monitor data_in and trigger RPI on new files; lab journal.
"""

from .watchdog import SentryWatchdog

__all__ = ["SentryWatchdog"]
