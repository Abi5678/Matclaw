"""
MatClaw CLI entrypoint — works from any directory.
Adds the project root to sys.path before importing the CLI module.
"""
import sys
import pathlib

# Ensure project root (containing src/) is in sys.path
# This file lives at: <project>/src/matclaw_cli.py
# So project root is two levels up
_root = pathlib.Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from matclaw.cli.main import main  # noqa: E402

if __name__ == "__main__":
    main()
