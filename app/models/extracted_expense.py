from typing import Optional, Literal
from pydantic import BaseModel, Field


class ExtractedExpense(BaseModel):
    """
    Structired expense data extracted from a user message.
    """
    amount: Optional[float] = Field(None, description="The amount of the expense.")
    currency: Optional[str] = Field(None, description="The currency of the expense.")
    category: Optional[Literal["food", "transport", "shopping", "health", "other"]] = Field(None, description="The category of the expense.")
    description: Optional[str] = Field(None, description="A description of the expense.")
    confidence: float = Field(ge=0.0, le=1.0, description="The confidence score of the extracted data, between 0 and 1.")