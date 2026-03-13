from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SessionRecord(BaseModel):
    """
    Minimal session-level memory record.

    This can later be extended / replaced by a dedicated vector store integration.
    """

    session_id: str = Field(..., description="Unique session identifier.")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    notes: List[str] = Field(default_factory=list, description="Freeform notes / events.")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        object.__setattr__(self, "updated_at", datetime.utcnow())

