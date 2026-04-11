from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    agent_name: str = Field(..., description="Name of the agent that produced this result.")
    success: bool = Field(..., description="Whether the agent executed successfully.")
    data: Dict[str, Any] = Field(default_factory=dict, description="Structured result data.")
    error_message: Optional[str] = Field(None, description="Human-readable error if success is False.")
