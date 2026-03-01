from typing import Dict, List, Optional
from enum import Enum
from sqlmodel import Field, SQLModel, Column, JSON

class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

class TonePreference(str, Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    ANALYTIC = "analytic"
    DIRECT = "direct"

class CEOPreferences(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True, unique=True)
    
    # Tone and Brevity
    preferred_tone: TonePreference = Field(default=TonePreference.CONCISE)
    max_summary_length: int = Field(default=500)  # chars/words target
    
    # Risk Tolerance by Domain
    risk_profile: Dict[str, RiskTolerance] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    
    # Decision Velocity (Low to High 1-10)
    decision_velocity: int = Field(default=5, ge=1, le=10)
    
    # Meeting Prioritization Behavior
    meeting_logic: List[str] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    )
    
    # Adaptive counters (Learning Loop)
    approval_count: int = Field(default=0)
    rejection_count: int = Field(default=0)
    edit_distance_avg: float = Field(default=0.0)

class StrategicInitiative(SQLModel):
    name: str
    owner: str
    status: str
    priority: int

class CompanyState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str = Field(index=True)
    last_updated: str  # ISO format
    
    # Decision Primitives
    revenue_segmentation: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    cost_structure: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    capital_position: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    strategic_initiatives: List[StrategicInitiative] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    )
    org_structure: Dict[str, str] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    regulatory_footprint: List[str] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    )
