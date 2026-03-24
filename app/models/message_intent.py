from typing import Literal
from pydantic import BaseModel, Field

class MessageIntent(BaseModel):
    """
    canonical clasification result for an inbound user message
    """
    intent: Literal['greeting', 'question', 'statement', 'command', 'expense', 'unknown']
    confidence: float = Field(ge=0.0, le=1.0)