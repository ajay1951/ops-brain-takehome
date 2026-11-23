from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class Goal(BaseModel):
    id: str
    title: str
    owner: str
    target_date: date
    priority_weight: int = Field(default=3, ge=1, le=3)
    health_status: str = "On Track"
    health_reason: str = "All tasks within target window."

class Task(BaseModel):
    id: str
    title: str
    owner: str
    due_date: date
    duration_days: int = Field(default=2, ge=1)
    status: str = "Proposed"
    goal_id: str
    blocked_by: List[str] = Field(default_factory=list)
    priority_score: float = 0.0
    priority_reason: str = ""
    nudge_count: int = 0
    approved_by: Optional[str] = None

class ExtractionResult(BaseModel):
    goals: List[Goal]
    tasks: List[Task]

class RunLog(BaseModel):
    run_id: str
    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    items_extracted: int
    approved_by: Optional[str] = None
