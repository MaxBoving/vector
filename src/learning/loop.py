from typing import Optional, Any, Dict
from datetime import datetime
from sqlmodel import Field, SQLModel, Column, JSON

class InteractionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    task_input: str
    routing_decision: str
    output: str
    
    # Feedback from Executive
    approval: bool = Field(default=False)
    edit_distance: int = Field(default=0)
    rejection_reason: Optional[str] = None
    
    # Store decision context for regression testing
    context_snapshot: Dict[str, Any] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )

class LearningLoop:
    """
    Continuous Improvement Loop.
    Tracks acceptance, edit distance, and reversals to refine 
    routing logic and context compression.
    """
    
    @staticmethod
    def process_feedback(log_id: int, approval: bool, edits: Optional[str] = None):
        """
        Updates preference model and regression harness based on interaction outcome.
        """
        pass
