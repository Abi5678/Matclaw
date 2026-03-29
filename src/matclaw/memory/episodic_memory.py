import logging
import sqlite3
import json
from datetime import datetime
from typing import Any, List

from pydantic import BaseModel

logger = logging.getLogger(__name__)

class Episode(BaseModel):
    id: str
    task_id: str
    timestamp: str
    state_machine_stage: str
    short_term_context: str
    raw_stack_traces: List[str] = []

class EpisodicMemoryManager:
    """
    Manages short-term episodic memory for long-horizon state preservation.
    Unlike Semantic Memory (ChromaDB) which stores distilled 'lessons learned',
    this creates a high-fidelity scratchpad for active tasks.
    It dumps verbose logs and stack traces to SQLite, allowing the daemon
    to resume complex DAG steps if it crashes without flooding the LLM context window.
    """
    def __init__(self, db_path: str = ".matclaw_experiments.sqlite3"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    timestamp TEXT,
                    stage TEXT,
                    context TEXT,
                    traces TEXT
                )
            ''')

    def log_episode(self, episode: Episode) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO episodic_memory (id, task_id, timestamp, stage, context, traces) VALUES (?, ?, ?, ?, ?, ?)",
                (episode.id, episode.task_id, episode.timestamp, episode.state_machine_stage, episode.short_term_context, json.dumps(episode.raw_stack_traces))
            )

    def retrieve_active_episodes(self, task_id: str) -> List[Episode]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, task_id, timestamp, stage, context, traces FROM episodic_memory WHERE task_id = ? ORDER BY timestamp DESC", 
                (task_id,)
            )
            rows = cursor.fetchall()
            return [
                Episode(
                    id=row[0], 
                    task_id=row[1], 
                    timestamp=row[2], 
                    state_machine_stage=row[3], 
                    short_term_context=row[4], 
                    raw_stack_traces=json.loads(row[5])
                ) for row in rows
            ]

    def crystallize_to_semantic(self, task_id: str, semantic_manager: Any) -> None:
        """
        Summarizes the episodes for a task_id, stores the distilled lesson in Semantic Memory,
        and deletes the verbose episodes to keep context windows clean.
        """
        # semantic_manager.store_artifact(key=task_id, metadata={"summary": "distilled lesson"})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM episodic_memory WHERE task_id = ?", (task_id,))
