import pytest
import sys
import os

# Ensure the repo root is on the path so "src.matclaw.*" imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
