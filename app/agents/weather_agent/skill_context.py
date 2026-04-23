from dataclasses import dataclass, field
from typing import Optional

from app.models.parsed_message import ParsedMessage


@dataclass(frozen=True)
class SkillContext:
    """Immutable input bag passed to every WeatherSkill.execute()."""
    user: dict
    inbound_text: str
    parsed: Optional[ParsedMessage] = None
    payload: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    """Returned by every WeatherSkill.execute(). Agent wraps this into AgentResult."""
    success: bool
    data: dict = field(default_factory=dict)
    error_message: Optional[str] = None
