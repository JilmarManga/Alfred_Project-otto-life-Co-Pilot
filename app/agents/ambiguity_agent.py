from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult


class AmbiguityAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        return AgentResult(
            agent_name="AmbiguityAgent",
            success=True,
            data={
                "raw_message": parsed.raw_message,
                "signals": parsed.signals,
            },
        )
