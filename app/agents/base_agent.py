from abc import ABC, abstractmethod
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult


class BaseAgent(ABC):

    @abstractmethod
    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        """Execute agent logic and return a structured result."""
        ...
