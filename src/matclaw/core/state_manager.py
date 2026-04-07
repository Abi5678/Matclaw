import logging
import json
import threading
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ExecutionState(str, Enum):
    IDLE = "IDLE"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    IMPLEMENTING = "IMPLEMENTING"
    VERIFYING = "VERIFYING"
    SLEEPING = "SLEEPING"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"

class AsyncStateTracker:
    """
    Saves the internal state of the RPI Executor or DAG Router to a JSON file.
    This enables real Long Horizon capability: the daemon can sleep to wait for
    a user response in Telegram, or for a multi-hour simulation to complete on a test rig.
    When the daemon restarts, it loads the state and resumes.
    """
    def __init__(self, state_file: str = ".matclaw_async_state.json"):
        self.state_file = Path(state_file)
        self._lock = threading.Lock()
        self.state_cache: Dict[str, Any] = {}
        
        if self.state_file.exists():
            try:
                raw = self.state_file.read_text().strip()
                if raw:
                    self.state_cache = json.loads(raw)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Corrupted state file %s, resetting: %s", self.state_file, e)
                self.state_cache = {}

    def save_task_state(self, task_id: str, state: ExecutionState, payload: Dict[str, Any]) -> None:
        with self._lock:
            self.state_cache[task_id] = {
                "status": state.value,
                "payload": payload
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state_cache, indent=2))
            tmp.replace(self.state_file)

    def load_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.state_cache.get(task_id)

    def is_task_suspended(self, task_id: str) -> bool:
        state = self.load_task_state(task_id)
        if not state:
            return False
        return state["status"] == ExecutionState.SLEEPING.value
